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
            if spk != cur_spk or gap > max_gap or dur > max_len:
                flush()
            elif _SENT_END.search(cur[-1].text) and dur >= min_sentence:
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

    Returns (i, j) inclusive word indices, or None if no reasonable match.
    """
    q = normalize(quote).replace(",", " ").replace(".", " ").split()
    if not q or not words:
        return None
    toks = [normalize(w.text).replace(",", " ").replace(".", " ").strip() for w in words]
    # Expand multi-token words ("d'un" -> "d un") keeping the word index.
    flat: list[tuple[str, int]] = []
    for i, t in enumerate(toks):
        for p in t.split():
            flat.append((p, i))
    if not flat:
        return None
    n = len(q)
    best = (0.0, None)
    first, last = q[0], q[-1]
    qset = {}
    for t in q:
        qset[t] = qset.get(t, 0) + 1
    for s in range(len(flat)):
        if flat[s][0] != first and (s == 0 or flat[s - 1][0] != first):
            continue
        e = min(len(flat), s + n + 4)
        window = [t for t, _ in flat[s:e]]
        # bag-of-words overlap with order bonus
        cnt = dict(qset)
        hit = 0
        for t in window[: n + 2]:
            if cnt.get(t, 0) > 0:
                cnt[t] -= 1
                hit += 1
        score = hit / n
        if score > best[0]:
            # end at the last occurrence of the last query token inside the window
            end = s + min(n, len(window)) - 1
            for k in range(min(len(window), n + 3) - 1, -1, -1):
                if window[k] == last:
                    end = s + k
                    break
            best = (score, (flat[s][1], flat[end][1]))
    if best[0] >= 0.6 and best[1] is not None:
        return best[1]
    return None
