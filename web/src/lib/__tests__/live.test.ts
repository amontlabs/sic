import { describe, expect, it } from "vitest";
import { deriveLiveView, initialLiveState, liveReducer, type LiveAction, type LiveState } from "../live";
import { parseSseData } from "../useLiveStream";
import { fixtureEvents, loadFixture } from "../../test/fixture";
import type { SicEvent } from "../../types";

const apply = (events: SicEvent[], state: LiveState = initialLiveState) =>
  events.reduce((s, event) => liveReducer(s, { type: "event", event, receivedAt: 0 }), state);

const run = loadFixture();
const claim = (id: string) => run.claims.find((c) => c.id === id)!;
const verdict = (id: string) => run.verdicts.find((v) => v.claim_id === id)!;
const ev = (seq: number, type: SicEvent["type"], at: number, data: object): SicEvent => ({
  seq,
  type,
  t: at,
  at,
  data: data as Record<string, unknown>,
});

describe("liveReducer", () => {
  it("names anonymous voice clusters from pipeline speaker mappings", () => {
    const seg = { id: "s00001", start: 1, end: 3, text: "Bonsoir", speaker: "spk0", cluster: "spk0", words: [] };
    const before = apply([ev(1, "segment", 4, seg)]);
    expect(deriveLiveView(before, 10, 0).lastSegment?.speaker).toBe("spk0");
    const after = apply([ev(2, "speaker", 6, { mapping: { spk0: "lepen", spk1: "macron" } })], before);
    expect(after.clusters.spk0?.value).toBe("lepen");
    expect(deriveLiveView(after, 10, 0).lastSegment?.speaker).toBe("lepen");
    // the mapping is also subject to the TV delay
    expect(deriveLiveView(after, 10, 5).lastSegment?.speaker).toBe("spk0");
  });

  it("dedupes events by seq (reconnect replays)", () => {
    const events = fixtureEvents();
    const once = apply(events);
    const twice = apply([...events.slice(0, 10), ...events], once);
    expect(twice.claimOrder).toEqual(once.claimOrder);
    expect(twice.segments).toHaveLength(10);
    expect(twice.seen.size).toBe(events.length);
    // identical state object when a duplicate arrives
    expect(liveReducer(once, { type: "event", event: events[3]!, receivedAt: 1 })).toBe(once);
  });

  it("links a verdict to its claim, including a verdict that arrives before the claim", () => {
    // fixture: seq 3 = verdict c0001 (at 15.2) precedes seq 4 = claim c0001 (at 16.0)
    const events = fixtureEvents();
    expect(events[2]!.type).toBe("verdict");
    expect(events[3]!.type).toBe("claim");
    const s1 = apply(events.slice(0, 3));
    expect(s1.verdicts.c0001?.value.label).toBe("plutot_exact");
    expect(s1.claimOrder).toEqual([]);
    expect(deriveLiveView(s1, 100, 0).items).toHaveLength(0); // orphan verdict not shown
    const s2 = apply(events.slice(3, 4), s1);
    const view = deriveLiveView(s2, 100, 0);
    expect(view.items[0]!.claim.id).toBe("c0001");
    expect(view.items[0]!.verdict?.label).toBe("plutot_exact");
  });

  it("handles delivery out of seq order", () => {
    const shuffled = [...fixtureEvents()].reverse();
    const s = apply(shuffled);
    const view = deriveLiveView(s, 1000, 0);
    expect(view.items.map((i) => i.claim.id)).toEqual(["c0007", "c0006", "c0005", "c0004", "c0003", "c0002", "c0001"]);
    expect(view.items.filter((i) => i.verdict)).toHaveLength(6);
    expect(s.segments.map((x) => x.value.id)).toEqual(run.segments.map((x) => x.id)); // sorted by start
  });

  it("keeps the newest verdict revision (higher seq wins)", () => {
    const v = verdict("c0003");
    let s = apply([ev(1, "claim", 1, claim("c0003")), ev(5, "verdict", 2, { ...v, label: "faux", reviewed: false })]);
    s = apply([ev(9, "verdict", 3, { ...v, label: "trompeur", reviewed: true })], s);
    expect(s.verdicts.c0003?.value.label).toBe("trompeur");
    s = apply([ev(7, "verdict", 4, { ...v, label: "exact" })], s); // stale revision
    expect(s.verdicts.c0003?.value.label).toBe("trompeur");
  });

  it("tracks the stream clock and end from status events (never deduped)", () => {
    let s = liveReducer(initialLiveState, { type: "event", event: ev(0, "status", 3, { state: "clock", t: 3 }), receivedAt: 50 });
    s = liveReducer(s, { type: "event", event: ev(0, "status", 4, { state: "clock", t: 4 }), receivedAt: 60 });
    expect(s.clock).toEqual({ t: 4, receivedAt: 60 });
    expect(s.connection).toBe("open");
    s = liveReducer(s, { type: "event", event: ev(0, "status", 5, { state: "end" }), receivedAt: 70 });
    expect(s.ended).toBe(true);
    expect(s.connection).toBe("ended");
    const after: LiveAction = { type: "connection", state: "reconnecting" };
    expect(liveReducer(s, after).connection).toBe("ended");
  });

  it("applies topic and speaker events", () => {
    const s = apply([
      ev(1, "topic", 1, { topic: "retraites" }),
      ev(2, "segment", 2, { ...run.segments[1]!, speaker: "spk3" }),
      ev(3, "speaker", 3, { segment_id: run.segments[1]!.id, speaker: "candidat-a" }),
      ev(4, "speaker", 4, { id: "spk7", name: "Invité mystère" }),
    ]);
    const view = deriveLiveView(s, 10, 0);
    expect(view.topic).toBe("retraites");
    expect(view.lastSegment?.speaker).toBe("candidat-a");
    expect(s.people.spk7?.value.name).toBe("Invité mystère");
    expect(deriveLiveView(s, 2.5, 0).lastSegment?.speaker).toBe("spk3"); // relabel not due yet
  });

  it("ignores malformed payloads", () => {
    const s = apply([ev(1, "claim", 1, { nope: true }), ev(2, "verdict", 1, {}), ev(3, "topic", 1, {})]);
    expect(s.claimOrder).toEqual([]);
    expect(Object.keys(s.verdicts)).toEqual([]);
    expect(s.topics).toEqual([]);
  });
});

describe("deriveLiveView with TV delay", () => {
  const s = apply(fixtureEvents());

  it("shows nothing before the first claim is available", () => {
    const view = deriveLiveView(s, 15.9, 0);
    expect(view.items).toHaveLength(0);
    expect(view.lastSegment?.id).toBe("s0001");
  });

  it("gates claims and verdicts separately by at + delay", () => {
    // c0002 claim at 22, verdict at 26.5
    const v1 = deriveLiveView(s, 23, 0);
    expect(v1.items[0]!.claim.id).toBe("c0002");
    expect(v1.items[0]!.verdict).toBeUndefined(); // "Vérification en cours…"
    expect(deriveLiveView(s, 26.5, 0).items[0]!.verdict?.label).toBe("exact");
  });

  it("a positive delay defers display", () => {
    expect(deriveLiveView(s, 23, 10).items).toHaveLength(0);
    expect(deriveLiveView(s, 26, 10).items.map((i) => i.claim.id)).toEqual(["c0001"]);
    const v = deriveLiveView(s, 26, 10);
    expect(v.pending).toBeGreaterThan(0);
  });

  it("orders newest first and falls back to the latest claim topic", () => {
    const v = deriveLiveView(s, 1000, 0);
    expect(v.items[0]!.claim.id).toBe("c0007");
    expect(v.topic).toBe("énergie");
    expect(v.pending).toBe(0);
  });
});

describe("parseSseData", () => {
  it("accepts a full Event JSON", () => {
    const e = parseSseData("claim", JSON.stringify(fixtureEvents()[3]));
    expect(e?.type).toBe("claim");
    expect(e?.seq).toBe(4);
  });
  it("wraps a bare status payload", () => {
    const e = parseSseData("status", '{"state":"clock","t":12.5}');
    expect(e).toMatchObject({ type: "status", t: 12.5, at: 12.5, data: { state: "clock", t: 12.5 } });
  });
  it("rejects garbage", () => {
    expect(parseSseData("claim", "not json")).toBeNull();
    expect(parseSseData("weird", "{}")).toBeNull();
  });
});
