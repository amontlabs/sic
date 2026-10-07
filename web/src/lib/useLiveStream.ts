import { useEffect, useReducer, useRef, useState } from "react";
import type { EventType, SicEvent } from "../types";
import { liveUrl } from "./api";
import { initialLiveState, liveReducer, type LiveState } from "./live";
import { estimateClock } from "./sync";

const EVENT_TYPES: EventType[] = ["segment", "claim", "verdict", "speaker", "topic", "status"];

/**
 * Normalises one SSE payload. docs/API.md says `data` is the Event JSON, but we also accept a
 * bare payload (e.g. `{state:"clock", t}` for status) and wrap it.
 */
export function parseSseData(eventName: string, raw: string): SicEvent | null {
  let obj: unknown;
  try {
    obj = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!obj || typeof obj !== "object") return null;
  const o = obj as Record<string, unknown>;
  if (typeof o.type === "string" && o.data && typeof o.data === "object") {
    return {
      seq: typeof o.seq === "number" ? o.seq : -1,
      type: o.type as EventType,
      t: typeof o.t === "number" ? o.t : 0,
      at: typeof o.at === "number" ? o.at : typeof o.t === "number" ? o.t : 0,
      data: o.data as Record<string, unknown>,
    };
  }
  const type = (EVENT_TYPES as string[]).includes(eventName) ? (eventName as EventType) : null;
  if (!type) return null;
  const t = typeof o.t === "number" ? o.t : 0;
  return { seq: typeof o.seq === "number" ? o.seq : -1, type, t, at: t, data: o };
}

export interface LiveStream {
  state: LiveState;
  /** best estimate of the stream (media) clock, seconds */
  clock: number;
}

/**
 * Connects to GET /api/debates/{id}/live. Reconnects with backoff, resuming from the last known
 * clock (events are deduped by seq in the reducer). Changing `speed` reconnects from the current clock.
 */
export function useLiveStream(id: string, from: number, speed: number, enabled = true): LiveStream {
  const [state, dispatch] = useReducer(liveReducer, initialLiveState);
  const [now, setNow] = useState(() => performance.now());
  const stateRef = useRef(state);
  stateRef.current = state;
  /** last clock position confirmed by the server (tick or event), used to resume after a drop */
  const confirmedRef = useRef(from);
  /** displayed clock never goes backwards within one session (from/speed) */
  const monoRef = useRef({ key: "", value: from });

  // ticking for clock interpolation (4 Hz is plenty for card gating)
  useEffect(() => {
    const iv = window.setInterval(() => setNow(performance.now()), 250);
    return () => window.clearInterval(iv);
  }, []);

  useEffect(() => {
    if (!enabled || !id) return;
    let es: EventSource | null = null;
    let retry = 0;
    let timer = 0;
    let closed = false;
    const startFrom = from;
    confirmedRef.current = from;

    const connect = (fromT: number) => {
      if (closed) return;
      dispatch({ type: "connection", state: retry === 0 ? "connecting" : "reconnecting" });
      es = new EventSource(liveUrl(id, fromT, speed));
      es.onopen = () => {
        retry = 0;
        dispatch({ type: "connection", state: "open", message: null });
      };
      const handler = (name: string) => (e: MessageEvent<string>) => {
        const ev = parseSseData(name, e.data);
        if (!ev) return;
        dispatch({ type: "event", event: ev, receivedAt: performance.now() });
        if (ev.type === "status" && ev.data.state === "end") {
          closed = true;
          es?.close();
        }
      };
      for (const t of EVENT_TYPES) es.addEventListener(t, handler(t) as EventListener);
      es.onmessage = handler("message");
      es.onerror = () => {
        es?.close();
        if (closed || stateRef.current.ended) return;
        retry++;
        const delay = Math.min(10_000, 1000 * 2 ** Math.min(retry - 1, 4));
        dispatch({ type: "connection", state: "reconnecting", message: "Connexion perdue, reconnexion…" });
        timer = window.setTimeout(() => connect(Math.max(0, confirmedRef.current - 1)), delay);
      };
    };
    connect(startFrom);
    return () => {
      closed = true;
      window.clearTimeout(timer);
      es?.close();
    };
  }, [id, from, speed, enabled]);

  const est = estimateClock(state.clock, now, speed, state.ended ? Number.POSITIVE_INFINITY : undefined);
  confirmedRef.current = Math.max(from, state.clock?.t ?? 0, state.maxAt);
  const key = `${id}|${from}|${speed}`;
  if (monoRef.current.key !== key) monoRef.current = { key, value: from };
  const clock = Math.max(monoRef.current.value, est ?? from, state.maxAt);
  monoRef.current.value = clock;
  return { state, clock };
}
