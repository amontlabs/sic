import { describe, expect, it } from "vitest";
import { clamp, estimateClock, findActiveIndex, isDue } from "../sync";
import { loadFixture } from "../../test/fixture";

const segs = [
  { start: 0, end: 5 },
  { start: 5, end: 9 },
  { start: 12, end: 20 }, // gap 9 → 12
  { start: 30, end: 31 },
];

describe("findActiveIndex (binary search)", () => {
  it("returns -1 for an empty list or before the first segment", () => {
    expect(findActiveIndex([], 3)).toBe(-1);
    expect(findActiveIndex([{ start: 2, end: 3 }], 1.9)).toBe(-1);
  });

  it("finds the segment containing t, boundaries belong to the next segment", () => {
    expect(findActiveIndex(segs, 0)).toBe(0);
    expect(findActiveIndex(segs, 4.99)).toBe(0);
    expect(findActiveIndex(segs, 5)).toBe(1);
    expect(findActiveIndex(segs, 15)).toBe(2);
  });

  it("holds the previous segment briefly across a gap, then releases", () => {
    expect(findActiveIndex(segs, 10)).toBe(1); // 1 s after end, within holdGap=2
    expect(findActiveIndex(segs, 11.5)).toBe(-1);
    expect(findActiveIndex(segs, 11.5, 5)).toBe(1);
    expect(findActiveIndex(segs, 25)).toBe(-1);
    expect(findActiveIndex(segs, 1000)).toBe(-1);
  });

  it("matches a linear scan on the fixture and on a large synthetic transcript", () => {
    const linear = (list: { start: number; end: number }[], t: number) => {
      let idx = -1;
      list.forEach((s, i) => {
        if (s.start <= t) idx = i;
      });
      if (idx < 0) return -1;
      return t < list[idx]!.end + 2 ? idx : -1;
    };
    const fixture = loadFixture().segments;
    for (let t = -1; t < 90; t += 0.37) expect(findActiveIndex(fixture, t)).toBe(linear(fixture, t));
    const big = Array.from({ length: 5000 }, (_, i) => ({ start: i * 3, end: i * 3 + 2.5 }));
    for (const t of [0, 1.2, 2.6, 2999.9, 7500.4, 14_999, 20_000]) expect(findActiveIndex(big, t)).toBe(linear(big, t));
  });
});

describe("TV delay gating", () => {
  it("shows an event once clock >= at + delay", () => {
    expect(isDue(10, 10, 0)).toBe(true);
    expect(isDue(10, 9.99, 0)).toBe(false);
    expect(isDue(10, 14, 5)).toBe(false);
    expect(isDue(10, 15, 5)).toBe(true);
  });
  it("negative delay shows events earlier", () => {
    expect(isDue(10, 5, -5)).toBe(true);
    expect(isDue(10, 4, -5)).toBe(false);
  });
});

describe("estimateClock", () => {
  it("returns null before the first tick", () => {
    expect(estimateClock(null, 1000, 1)).toBeNull();
  });
  it("advances at `speed` from the last tick", () => {
    expect(estimateClock({ t: 10, receivedAt: 1000 }, 1500, 1)).toBeCloseTo(10.5);
    expect(estimateClock({ t: 10, receivedAt: 1000 }, 1500, 10)).toBeCloseTo(15);
  });
  it("caps drift when ticks stop arriving", () => {
    expect(estimateClock({ t: 10, receivedAt: 0 }, 60_000, 1)).toBe(12);
    expect(estimateClock({ t: 10, receivedAt: 0 }, 60_000, 1, Infinity)).toBe(70);
  });
  it("clamp", () => {
    expect(clamp(40, -30, 30)).toBe(30);
    expect(clamp(-40, -30, 30)).toBe(-30);
    expect(clamp(3, -30, 30)).toBe(3);
  });
});
