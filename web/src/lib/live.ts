import type { Claim, Person, Segment, SicEvent, Verdict } from "../types";
import { isDue } from "./sync";

/** Something that arrived on the stream, with the media time it became available. */
export interface Stamped<T> {
  value: T;
  at: number;
  seq: number;
}

export type ConnectionState = "idle" | "connecting" | "open" | "reconnecting" | "ended" | "error";

export interface LiveState {
  seen: ReadonlySet<number>;
  segments: Stamped<Segment>[];
  claims: Record<string, Stamped<Claim>>;
  /** claim ids in arrival order */
  claimOrder: string[];
  /** keyed by claim_id; may arrive before its claim (out of order) */
  verdicts: Record<string, Stamped<Verdict>>;
  topics: Stamped<string>[];
  /** speaker identity updates: cluster id → person (e.g. "spk3" identified as "lepen") */
  people: Record<string, Stamped<Pick<Person, "id" | "name"> & Partial<Person>>>;
  /** segment relabels: segment id → speaker id */
  relabels: Record<string, Stamped<string>>;
  /** voice cluster → roster id, from the pipeline's `speaker` events ({ mapping: { spk0: "lepen" } }) */
  clusters: Record<string, Stamped<string>>;
  clock: { t: number; receivedAt: number } | null;
  /** highest `at` seen; the clock is at least this */
  maxAt: number;
  connection: ConnectionState;
  ended: boolean;
  message: string | null;
}

export const initialLiveState: LiveState = {
  seen: new Set(),
  segments: [],
  claims: {},
  claimOrder: [],
  verdicts: {},
  topics: [],
  people: {},
  relabels: {},
  clusters: {},
  clock: null,
  maxAt: 0,
  connection: "idle",
  ended: false,
  message: null,
};

export type LiveAction =
  | { type: "event"; event: SicEvent; receivedAt: number }
  | { type: "connection"; state: ConnectionState; message?: string | null }
  | { type: "reset" };

const str = (v: unknown): string | null => (typeof v === "string" && v.length > 0 ? v : null);

export function liveReducer(state: LiveState, action: LiveAction): LiveState {
  switch (action.type) {
    case "reset":
      return initialLiveState;
    case "connection":
      return {
        ...state,
        connection: state.ended && action.state !== "connecting" ? "ended" : action.state,
        message: action.message === undefined ? state.message : action.message,
        ...(action.state === "connecting" ? { ended: false } : {}),
      };
    case "event":
      return applyEvent(state, action.event, action.receivedAt);
  }
}

function applyEvent(state: LiveState, ev: SicEvent, receivedAt: number): LiveState {
  // Clock ticks are not unique facts; they are never deduped by seq (servers may reuse seq for them).
  if (ev.type === "status") {
    const s = str(ev.data.state);
    if (s === "clock") {
      const t = typeof ev.data.t === "number" ? ev.data.t : ev.t;
      return { ...state, clock: { t, receivedAt }, connection: "open" };
    }
    if (s === "end") return { ...state, ended: true, connection: "ended", message: null };
    return { ...state, message: str(ev.data.message) ?? s };
  }
  if (state.seen.has(ev.seq)) return state;
  const seen = new Set(state.seen);
  seen.add(ev.seq);
  const base = { ...state, seen, maxAt: Math.max(state.maxAt, ev.at) };
  const stamp = <T,>(value: T): Stamped<T> => ({ value, at: ev.at, seq: ev.seq });

  switch (ev.type) {
    case "segment": {
      const seg = ev.data as unknown as Segment;
      if (!str(seg.id)) return base;
      const without = base.segments.filter((s) => s.value.id !== seg.id); // re-sent segment = correction
      const segments = [...without, stamp(seg)].sort((a, b) => a.value.start - b.value.start || a.seq - b.seq);
      return { ...base, segments };
    }
    case "claim": {
      const claim = ev.data as unknown as Claim;
      if (!str(claim.id)) return base;
      const known = claim.id in base.claims;
      return {
        ...base,
        claims: { ...base.claims, [claim.id]: known ? { ...stamp(claim), at: base.claims[claim.id]!.at } : stamp(claim) },
        claimOrder: known ? base.claimOrder : [...base.claimOrder, claim.id],
      };
    }
    case "verdict": {
      const v = ev.data as unknown as Verdict;
      if (!str(v.claim_id)) return base;
      const prev = base.verdicts[v.claim_id];
      if (prev && prev.seq > ev.seq) return base; // keep the newest revision
      return { ...base, verdicts: { ...base.verdicts, [v.claim_id]: stamp(v) } };
    }
    case "topic": {
      const topic = str(ev.data.topic) ?? str(ev.data.label) ?? str(ev.data.name) ?? str(ev.data.title);
      return topic ? { ...base, topics: [...base.topics, stamp(topic)] } : base;
    }
    case "speaker": {
      const mapping = ev.data.mapping;
      if (mapping && typeof mapping === "object" && !Array.isArray(mapping)) {
        const clusters = { ...base.clusters };
        for (const [cluster, person] of Object.entries(mapping as Record<string, unknown>)) {
          const pid = str(person);
          if (pid) clusters[cluster] = stamp(pid);
        }
        return { ...base, clusters };
      }
      const segId = str(ev.data.segment_id);
      const speaker = str(ev.data.speaker);
      if (segId && speaker) return { ...base, relabels: { ...base.relabels, [segId]: stamp(speaker) } };
      const id = str(ev.data.id) ?? str(ev.data.cluster) ?? speaker;
      const name = str(ev.data.name);
      if (id && name) {
        return { ...base, people: { ...base.people, [id]: stamp({ ...(ev.data as Partial<Person>), id, name }) } };
      }
      return base;
    }
    default:
      return base;
  }
}

export interface LiveItem {
  claim: Claim;
  verdict: Verdict | undefined;
  at: number;
}

export interface LiveView {
  /** visible claims, newest first */
  items: LiveItem[];
  lastSegment: Segment | null;
  topic: string | null;
  /** events received but held back by the TV delay */
  pending: number;
}

/** What the viewer should see at stream clock `clock` with TV delay `delay`. */
export function deriveLiveView(state: LiveState, clock: number, delay: number): LiveView {
  const due = (x: { at: number }) => isDue(x.at, clock, delay);
  let pending = 0;
  const items: LiveItem[] = [];
  for (const id of state.claimOrder) {
    const c = state.claims[id]!;
    if (!due(c)) {
      pending++;
      continue;
    }
    const v = state.verdicts[id];
    const verdictDue = v && due(v);
    if (v && !verdictDue) pending++;
    items.push({ claim: c.value, verdict: verdictDue ? v.value : undefined, at: c.at });
  }
  items.sort((a, b) => b.claim.start - a.claim.start || b.at - a.at);

  let lastSegment: Segment | null = null;
  for (const s of state.segments) {
    if (!due(s)) {
      pending++;
      continue;
    }
    const relabel = state.relabels[s.value.id];
    const clusterId = str((s.value as Segment & { cluster?: string }).cluster) ?? s.value.speaker ?? "";
    const named = state.clusters[clusterId];
    const seg = relabel && due(relabel)
      ? { ...s.value, speaker: relabel.value }
      : named && due(named) ? { ...s.value, speaker: named.value } : s.value;
    if (!lastSegment || seg.start >= lastSegment.start) lastSegment = seg;
  }

  let topic: string | null = null;
  for (const tp of state.topics) if (due(tp)) topic = tp.value;
  if (!topic) topic = items[0]?.claim.topic ?? null; // fallback: topic of the latest claim

  return { items, lastSegment, topic, pending };
}
