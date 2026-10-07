/** Time-sync helpers shared by the replay and live pages. Pure functions, unit tested. */

export interface Timed {
  start: number;
  end: number;
}

/**
 * Index of the segment playing at time `t` (segments sorted by start).
 * Binary search for the last segment whose start <= t. If t falls in a gap after that
 * segment ended, it stays active for `holdGap` seconds (so highlight doesn't flicker
 * between sentences), then -1.
 */
export function findActiveIndex(segments: readonly Timed[], t: number, holdGap = 2): number {
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    const seg = segments[mid]!;
    if (seg.start <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (found === -1) return -1;
  const seg = segments[found]!;
  return t < seg.end + holdGap ? found : -1;
}

/** Event gating for the TV delay: shown once the stream clock reaches event.at + delay. */
export function isDue(eventAt: number, clock: number, delay: number): boolean {
  return clock >= eventAt + delay;
}

/** Clamp helper. */
export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/**
 * Stream clock estimate between server `status: clock` ticks: last tick + elapsed wall time × speed,
 * never more than `maxDrift` media seconds ahead of the last tick (the stream may have stalled).
 */
export function estimateClock(
  lastTick: { t: number; receivedAt: number } | null,
  now: number,
  speed: number,
  maxDrift = 2 * speed,
): number | null {
  if (!lastTick) return null;
  const elapsed = Math.max(0, (now - lastTick.receivedAt) / 1000) * speed;
  return lastTick.t + Math.min(elapsed, maxDrift);
}
