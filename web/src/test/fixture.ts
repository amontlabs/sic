import raw from "../../../data/fixtures/sample-run.json";
import type { Run, SicEvent } from "../types";

/** The shared fixture (data/fixtures/sample-run.json), typed. Cloned per call. */
export function loadFixture(): Run {
  return structuredClone(raw) as unknown as Run;
}

export function fixtureEvents(): SicEvent[] {
  return loadFixture().events ?? [];
}
