import type { Claim, Verdict, VerdictLabel } from "../types";
import { isNotable } from "./verdict";

export type Scope = "notables" | "all";

export interface ClaimFilter {
  scope: Scope;
  speaker: string | null;
  label: VerdictLabel | null;
}

export const DEFAULT_FILTER: ClaimFilter = { scope: "notables", speaker: null, label: null };

/**
 * - label set → only that verdict (scope ignored)
 * - "notables" → imprécis / trompeur / faux, plus checkable claims still being verified
 *   (they may turn out notable; hidden once an unremarkable verdict lands)
 * - "all" → everything, including non-checkable claims
 * - speaker set → restricted to that speaker
 */
export function matchesFilter(claim: Claim, verdict: Verdict | undefined, f: ClaimFilter): boolean {
  if (f.speaker && claim.speaker !== f.speaker) return false;
  if (f.label) return verdict?.label === f.label;
  if (f.scope === "all") return true;
  if (verdict) return isNotable(verdict.label);
  return claim.checkable;
}

export function filterClaims<T extends { claim: Claim; verdict: Verdict | undefined }>(items: readonly T[], f: ClaimFilter): T[] {
  return items.filter((it) => matchesFilter(it.claim, it.verdict, f));
}

/** Filter state <-> URL search params (so filtered views are shareable). */
export function filterFromParams(p: URLSearchParams): ClaimFilter {
  const scope = p.get("vue") === "tout" ? "all" : "notables";
  const label = p.get("verdict") as VerdictLabel | null;
  return { scope, speaker: p.get("orateur"), label: label || null };
}

export function filterToParams(f: ClaimFilter, base: URLSearchParams): URLSearchParams {
  const p = new URLSearchParams(base);
  if (f.scope === "all") p.set("vue", "tout");
  else p.delete("vue");
  if (f.speaker) p.set("orateur", f.speaker);
  else p.delete("orateur");
  if (f.label) p.set("verdict", f.label);
  else p.delete("verdict");
  return p;
}
