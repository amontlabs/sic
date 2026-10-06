"""Speaker separation.

Offline: sherpa-onnx pyannote segmentation + speaker embeddings + clustering.
Live: per-utterance embeddings assigned to running centroids (`OnlineSpeakerTracker`).

Clusters are anonymous ("spk0"...). Names come later from context (speakers.py),
never from a stored voiceprint database (biometric data, GDPR art. 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from sic import config
from sic.models import Word

SEG_MODEL = config.MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMB_MODEL = config.MODELS_DIR / "nemo_en_titanet_small.onnx"


@dataclass
class Turn:
    start: float
    end: float
    speaker: str


def _check_models() -> None:
    missing = [p for p in (SEG_MODEL, EMB_MODEL) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing diarization models: {missing}. Run scripts/fetch_models.sh")


def diarize(audio: np.ndarray, num_speakers: int | None = None, offset: float = 0.0) -> list[Turn]:
    import sherpa_onnx as so

    _check_models()
    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)), num_threads=8
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=8),
        clustering=so.FastClusteringConfig(num_clusters=num_speakers or -1, threshold=0.6),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    d = so.OfflineSpeakerDiarization(cfg)
    result = d.process(audio.astype(np.float32)).sort_by_start_time()
    return [Turn(round(r.start + offset, 3), round(r.end + offset, 3), f"spk{r.speaker}") for r in result]


def diarize_long(
    audio: np.ndarray,
    num_speakers: int | None = None,
    block: float = 600.0,
    link_threshold: float = 0.55,
    on_progress=None,
) -> list[Turn]:
    """Diarize long audio in blocks, then link block-local clusters into global speakers.

    Offline clustering over a whole 3-hour debate is slow (pairwise), so each block is clustered
    on its own and block clusters are merged by the cosine similarity of their voice centroids.
    """
    from sklearn.cluster import AgglomerativeClustering

    sr = 16000
    total = len(audio) / sr
    if total <= block * 1.5:
        turns = diarize(audio, num_speakers)
        if on_progress:
            on_progress(1.0)
        return turns
    emb = Embedder()
    local: list[tuple[str, list[Turn]]] = []  # (block-cluster key, turns)
    centroids: list[np.ndarray] = []
    t = 0.0
    bi = 0
    while t < total:
        a, b = int(t * sr), int(min(total, t + block) * sr)
        turns = diarize(audio[a:b], None, offset=t)
        by: dict[str, list[Turn]] = {}
        for tr in turns:
            by.setdefault(tr.speaker, []).append(tr)
        for spk, trs in by.items():
            # Centroid from the longest turns (up to ~60 s of speech).
            pieces, acc = [], 0.0
            for tr in sorted(trs, key=lambda x: -(x.end - x.start)):
                if acc > 60:
                    break
                pieces.append(audio[int(tr.start * sr) : int(tr.end * sr)])
                acc += tr.end - tr.start
            if acc < 1.0:
                continue
            centroids.append(emb.embed_many(pieces))
            local.append((f"b{bi}-{spk}", trs))
        t += block
        bi += 1
        if on_progress:
            on_progress(min(t, total) / total * 0.95)
    if len(centroids) == 1:
        labels = [0]
    else:
        X = np.stack(centroids)
        ac = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=1 - link_threshold
        )
        labels = list(ac.fit_predict(X))
        if num_speakers and len(set(labels)) < num_speakers <= len(X):
            labels = list(AgglomerativeClustering(n_clusters=num_speakers, metric="cosine", linkage="average").fit_predict(X))
    # Stable ids: biggest speaker first.
    dur: dict[int, float] = {}
    for (key, trs), lab in zip(local, labels, strict=True):
        dur[lab] = dur.get(lab, 0.0) + sum(tr.end - tr.start for tr in trs)
    order = {lab: i for i, lab in enumerate(sorted(dur, key=lambda k: -dur[k]))}
    out = [Turn(tr.start, tr.end, f"spk{order[lab]}") for (key, trs), lab in zip(local, labels, strict=True) for tr in trs]
    out.sort(key=lambda x: x.start)
    if on_progress:
        on_progress(1.0)
    return out


def assign_words(words: list[Word], turns: list[Turn]) -> list[str | None]:
    """Speaker for each word: the turn with max overlap, else the nearest turn (within 1 s)."""
    out: list[str | None] = []
    j = 0
    n = len(turns)
    for w in words:
        while j < n and turns[j].end < w.start - 5:
            j += 1
        best, best_ov, best_gap = None, 0.0, 1e9
        k = j
        while k < n and turns[k].start <= w.end + 5:
            t = turns[k]
            ov = min(w.end, t.end) - max(w.start, t.start)
            if ov > best_ov:
                best, best_ov = t.speaker, ov
            gap = max(t.start - w.end, w.start - t.end, 0.0)
            if best_ov <= 0 and gap < best_gap:
                best_gap = gap
                if gap <= 1.0:
                    best = t.speaker
            k += 1
        out.append(best)
    # Fill gaps from neighbours so every word gets a speaker.
    last = None
    for i, s in enumerate(out):
        if s is None:
            out[i] = last
        else:
            last = s
    nxt = None
    for i in range(len(out) - 1, -1, -1):
        if out[i] is None:
            out[i] = nxt
        else:
            nxt = out[i]
    return out


class Embedder:
    def __init__(self) -> None:
        import sherpa_onnx as so

        _check_models()
        self._ex = so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(model=str(EMB_MODEL), num_threads=4))

    MAX_SECONDS = 15.0  # the ONNX model fails on very long inputs

    def embed(self, audio: np.ndarray) -> np.ndarray:
        audio = audio[: int(self.MAX_SECONDS * 16000)]
        s = self._ex.create_stream()
        s.accept_waveform(16000, audio.astype(np.float32))
        s.input_finished()
        v = np.array(self._ex.compute(s), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def embed_many(self, pieces: list[np.ndarray]) -> np.ndarray:
        """Duration-weighted mean of per-piece embeddings."""
        acc = None
        for p in pieces:
            if len(p) < 8000:
                continue
            try:
                v = self.embed(p) * min(len(p) / 16000, self.MAX_SECONDS)
            except RuntimeError:
                continue
            acc = v if acc is None else acc + v
        if acc is None:
            return self.embed(np.concatenate(pieces))
        return acc / (np.linalg.norm(acc) + 1e-9)


@dataclass
class _Cluster:
    centroid: np.ndarray
    weight: float
    id: str


@dataclass
class OnlineSpeakerTracker:
    """Assign utterance embeddings to running speaker centroids."""

    threshold: float = 0.5  # cosine similarity to join an existing cluster
    max_speakers: int = 8
    clusters: list[_Cluster] = field(default_factory=list)

    def assign(self, emb: np.ndarray, duration: float) -> str:
        if self.clusters:
            sims = [float(c.centroid @ emb) for c in self.clusters]
            i = int(np.argmax(sims))
            if sims[i] >= self.threshold or len(self.clusters) >= self.max_speakers:
                c = self.clusters[i]
                w = min(duration, 10.0)
                v = c.centroid * c.weight + emb * w
                c.centroid = v / (np.linalg.norm(v) + 1e-9)
                c.weight += w
                return c.id
        cid = f"spk{len(self.clusters)}"
        self.clusters.append(_Cluster(emb, min(duration, 10.0), cid))
        return cid
