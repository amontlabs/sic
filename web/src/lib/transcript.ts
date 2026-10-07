import type { Claim, Segment } from "../types";

export interface Turn {
  id: string;
  speaker: string | null;
  start: number;
  end: number;
  /** indices into the flat segments array */
  segmentIndices: number[];
}

/** Consecutive segments by the same speaker form one turn. */
export function groupTurns(segments: readonly Segment[]): Turn[] {
  const turns: Turn[] = [];
  segments.forEach((seg, i) => {
    const last = turns[turns.length - 1];
    if (last && last.speaker === seg.speaker) {
      last.segmentIndices.push(i);
      last.end = seg.end;
    } else {
      turns.push({ id: `turn-${seg.id}`, speaker: seg.speaker, start: seg.start, end: seg.end, segmentIndices: [i] });
    }
  });
  return turns;
}

/** Claims attached to each segment id (via segment_ids, else by time overlap & speaker). */
export function claimsBySegment(segments: readonly Segment[], claims: readonly Claim[]): Map<string, Claim[]> {
  const map = new Map<string, Claim[]>();
  const push = (segId: string, c: Claim) => {
    const list = map.get(segId);
    if (list) {
      if (!list.includes(c)) list.push(c);
    } else map.set(segId, [c]);
  };
  for (const c of claims) {
    if (c.segment_ids.length > 0) {
      for (const id of c.segment_ids) push(id, c);
      continue;
    }
    for (const s of segments) {
      if (s.end > c.start && s.start < c.end && (!s.speaker || s.speaker === c.speaker)) push(s.id, c);
    }
  }
  return map;
}

const norm = (w: string) =>
  w
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^\p{L}\p{N}]/gu, "");

interface Tok {
  n: string;
  start: number;
  end: number;
}

function tokenize(text: string): Tok[] {
  const out: Tok[] = [];
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const n = norm(m[0]);
    if (n) out.push({ n, start: m.index, end: m.index + m[0].length });
  }
  return out;
}

/**
 * Character range of `quote` inside `text`, tolerant to case, accents, punctuation and to a
 * quote that spans several segments (we highlight the longest common run of words, min 3
 * words or the whole quote/segment). Returns null when nothing convincing matches.
 */
export function findQuoteRange(text: string, quote: string): [number, number] | null {
  const direct = text.indexOf(quote);
  if (direct >= 0) return [direct, direct + quote.length];
  const a = tokenize(text);
  const b = tokenize(quote).map((t) => t.n);
  if (a.length === 0 || b.length === 0) return null;
  // longest common substring over word tokens (small inputs: O(n*m) is fine)
  let best = 0;
  let bestEndA = -1;
  let prev = new Array<number>(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    const cur = new Array<number>(b.length + 1).fill(0);
    for (let j = 1; j <= b.length; j++) {
      if (a[i - 1]!.n === b[j - 1]) {
        cur[j] = prev[j - 1]! + 1;
        if (cur[j]! > best) {
          best = cur[j]!;
          bestEndA = i - 1;
        }
      }
    }
    prev = cur;
  }
  const minRun = Math.min(3, b.length, a.length);
  if (best < minRun) return null;
  const first = a[bestEndA - best + 1]!;
  const last = a[bestEndA]!;
  return [first.start, last.end];
}
