import { describe, expect, it } from "vitest";
import { DEFAULT_FILTER, filterClaims, filterFromParams, filterToParams, matchesFilter } from "../filters";
import { loadFixture } from "../../test/fixture";
import type { Claim, Verdict } from "../../types";

const run = loadFixture();
const items = run.claims.map((claim) => ({ claim, verdict: run.verdicts.find((v) => v.claim_id === claim.id) }));
const ids = (list: { claim: Claim }[]) => list.map((i) => i.claim.id);

describe("claim filters", () => {
  it("defaults to Notables: imprécis, trompeur, faux", () => {
    expect(DEFAULT_FILTER.scope).toBe("notables");
    expect(ids(filterClaims(items, DEFAULT_FILTER))).toEqual(["c0003", "c0004", "c0007"]);
  });

  it("Tout includes everything, non-checkable claims too", () => {
    expect(filterClaims(items, { scope: "all", speaker: null, label: null })).toHaveLength(7);
  });

  it("filters by speaker, combined with the scope", () => {
    expect(ids(filterClaims(items, { scope: "all", speaker: "candidat-a", label: null }))).toEqual(["c0001", "c0002", "c0006"]);
    expect(ids(filterClaims(items, { scope: "notables", speaker: "candidat-a", label: null }))).toEqual([]);
    expect(ids(filterClaims(items, { scope: "notables", speaker: "candidate-b", label: null }))).toEqual(["c0003", "c0004", "c0007"]);
  });

  it("filters by verdict label (overrides scope)", () => {
    expect(ids(filterClaims(items, { scope: "notables", speaker: null, label: "exact" }))).toEqual(["c0002", "c0006"]);
    expect(ids(filterClaims(items, { scope: "all", speaker: null, label: "trompeur" }))).toEqual([]);
  });

  it("keeps checkable claims still being verified under Notables, not non-checkable ones", () => {
    const pending = { ...run.claims[0]!, id: "x" };
    const promise = run.claims.find((c) => !c.checkable)!;
    expect(matchesFilter(pending, undefined, DEFAULT_FILTER)).toBe(true);
    expect(matchesFilter(promise, undefined, DEFAULT_FILTER)).toBe(false);
    const exact: Verdict = { ...run.verdicts[1]!, claim_id: "x" };
    expect(matchesFilter(pending, exact, DEFAULT_FILTER)).toBe(false);
  });

  it("round-trips through URL params", () => {
    const f = { scope: "all" as const, speaker: "candidate-b", label: "faux" as const };
    const p = filterToParams(f, new URLSearchParams("c=c0003&operator=1"));
    expect(p.get("c")).toBe("c0003");
    expect(p.get("operator")).toBe("1");
    expect(filterFromParams(p)).toEqual(f);
    expect(filterFromParams(filterToParams(DEFAULT_FILTER, p))).toEqual(DEFAULT_FILTER);
  });
});
