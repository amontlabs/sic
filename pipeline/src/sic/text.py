"""Turn timestamped words + speaker labels into readable segments."""

from __future__ import annotations

import re
import unicodedata

from sic.models import Segment, Word

_ATTACH_LEFT = re.compile(r"^(['’\-,.;:!?…)\]»%]|\.\.\.)")
_SENT_END = re.compile(r"[.?!…]$")


def join_words(words: list[Word] | list[str]) -> str:
    out = ""
    prev = ""
    for w in words:
        t = w if isinstance(w, str) else w.text
        if not t:
            continue
        if not out:
            out = t
        elif _ATTACH_LEFT.match(t) or prev.endswith(("'", "’", "-", "(", "«")):
            out += t
        else:
            out += " " + t
        prev = t
    out = re.sub(r"\s+([,.])", r"\1", out)
    out = out.replace("« ", "« ").replace(" »", " »")
    return out.strip()


def build_segments(
    words: list[Word],
    speakers: list[str | None],
    max_len: float = 20.0,
    min_sentence: float = 3.0,
    max_gap: float = 1.5,
    id_prefix: str = "s",
    start_index: int = 0,
) -> list[Segment]:
    """Split on speaker change, long pauses, sentence ends (if long enough) or max length."""
    segs: list[Segment] = []
    cur: list[Word] = []
    cur_spk: str | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            idx = start_index + len(segs)
            segs.append(
                Segment(
                    id=f"{id_prefix}{idx:05d}",
                    start=cur[0].start,
                    end=cur[-1].end,
                    text=join_words(cur),
                    speaker=cur_spk,
                    words=cur,
                )
            )
        cur = []

    for w, spk in zip(words, speakers, strict=True):
        if cur:
            gap = w.start - cur[-1].end
            dur = cur[-1].end - cur[0].start
            if spk != cur_spk or gap > max_gap or dur > max_len or _SENT_END.search(cur[-1].text) and dur >= min_sentence:
                flush()
        if not cur:
            cur_spk = spk
        cur.append(w)
    flush()
    return segs


def normalize(s: str) -> str:
    """Lowercase, strip accents and punctuation (for fuzzy matching)."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[’']", " ", s)
    s = re.sub(r"[^\w\s%,.]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def locate_quote(quote: str, words: list[Word]) -> tuple[int, int] | None:
    """Find the word span best matching `quote` (fuzzy, accent/punctuation-insensitive).

    Windows are anchored on any of the quote's first three tokens (the model may add a word like
    "Et", or ASR may garble the first one). Returns (i, j) inclusive word indices, or None.
    """
    q = normalize(quote).replace(",", " ").replace(".", " ").split()
    if not q or not words:
        return None
    toks = [normalize(w.text).replace(",", " ").replace(".", " ").strip() for w in words]
    flat: list[tuple[str, int]] = []  # sub-tokens ("d'un" -> "d", "un") keeping their word index
    for i, t in enumerate(toks):
        for part in t.split():
            flat.append((part, i))
    if not flat:
        return None
    n = len(q)
    qcount: dict[str, int] = {}
    for t in q:
        qcount[t] = qcount.get(t, 0) + 1
    anchors = {t: k for k, t in reversed(list(enumerate(q[:3])))}
    best_score, best_span = 0.0, None
    for pos, (tok, _) in enumerate(flat):
        k = anchors.get(tok)
        if k is None:
            continue
        s0 = max(0, pos - k)
        window = flat[s0 : s0 + n + 3]
        cnt = dict(qcount)
        hit = 0
        first = last = None
        for idx, (t, _) in enumerate(window):
            if cnt.get(t, 0) > 0:
                cnt[t] -= 1
                hit += 1
                if first is None:
                    first = idx
                last = idx
        score = hit / n
        if first is not None and score > best_score:
            best_score, best_span = score, (window[first][1], window[last][1])
    if best_score >= 0.6 and best_span is not None:
        return best_span
    return None
