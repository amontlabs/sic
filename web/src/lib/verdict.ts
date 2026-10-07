import { VERDICT_LABELS_FR, type Claim, type SourceKind, type Verdict, type VerdictLabel, type VerdictMethod } from "../types";

/** Display order: from "true" to "false", unverifiable last. */
export const VERDICT_ORDER: VerdictLabel[] = ["exact", "plutot_exact", "imprecis", "trompeur", "faux", "inverifiable"];

/** Verdicts surfaced by the default "Notables" filter. */
export const NOTABLE_LABELS: ReadonlySet<VerdictLabel> = new Set(["imprecis", "trompeur", "faux"]);

export function verdictLabel(label: VerdictLabel): string {
  return VERDICT_LABELS_FR[label] ?? label;
}

export function isNotable(label: VerdictLabel): boolean {
  return NOTABLE_LABELS.has(label);
}

export function reviewLabel(v: Pick<Verdict, "reviewed">): { short: string; long: string } {
  return v.reviewed
    ? { short: "Relu", long: "Verdict relu par un humain" }
    : { short: "Auto", long: "Verdict automatique, non relu" };
}

export const METHOD_LABELS: Record<VerdictMethod, string> = {
  library: "Vérification déjà publiée",
  data: "Données officielles",
  web: "Recherche de sources",
  none: "Sans source automatique",
};

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  official_data: "Données officielles",
  parliament: "Parlement",
  law: "Texte de loi",
  factcheck: "Vérification",
  library: "Bibliothèque Sic",
  web: "Web",
};

/** Canvas / non-CSS colors (share card). Kept in sync with the CSS tokens in index.css. */
export const VERDICT_HEX: Record<VerdictLabel, { fg: string; bg: string; solid: string }> = {
  exact: { fg: "#14532d", bg: "#dcf2e3", solid: "#1f7a43" },
  plutot_exact: { fg: "#2f5212", bg: "#e7f2d4", solid: "#5b8a1e" },
  imprecis: { fg: "#6b4407", bg: "#fbecc8", solid: "#b7791f" },
  trompeur: { fg: "#7c2d0a", bg: "#fde0cc", solid: "#c2540f" },
  faux: { fg: "#7f1d1d", bg: "#fbdcdc", solid: "#c0262d" },
  inverifiable: { fg: "#3f4650", bg: "#e6e8eb", solid: "#6b7280" },
};

/** Neutral tag for claims that are not checkable (promises, opinions). Never a verdict. */
export function nonCheckableTag(claim: Pick<Claim, "quote" | "claim" | "kind">): string {
  const text = `${claim.quote} ${claim.claim}`.toLowerCase();
  const promise =
    /\b(je|nous|on)\s+(ne\s+)?\w+(rai|rons|ra)\b/.test(text) ||
    /\b(je|nous)\s+(vais|allons)\b/.test(text) ||
    /\b(promet|propose|s'engage|engagement)\b/.test(text);
  return promise ? "Promesse · non vérifiable" : "Opinion · non vérifiable";
}

/** Status of a claim for display & filtering. */
export type ClaimStatus =
  | { kind: "verdict"; verdict: Verdict }
  | { kind: "pending" }
  | { kind: "not_checkable"; tag: string };

export function claimStatus(claim: Claim, verdict: Verdict | undefined): ClaimStatus {
  if (verdict) return { kind: "verdict", verdict };
  if (!claim.checkable) return { kind: "not_checkable", tag: nonCheckableTag(claim) };
  return { kind: "pending" };
}

export function countByLabel(verdicts: Iterable<Pick<Verdict, "label">>): Record<VerdictLabel, number> {
  const out = Object.fromEntries(VERDICT_ORDER.map((l) => [l, 0])) as Record<VerdictLabel, number>;
  for (const v of verdicts) out[v.label] = (out[v.label] ?? 0) + 1;
  return out;
}

/** True for a bare homepage link ("https://www.insee.fr/"), which is not a specific source page. */
export function isHomepageUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  try {
    const u = new URL(url);
    return (u.pathname === "" || u.pathname === "/") && !u.search && !u.hash;
  } catch {
    return false;
  }
}

/** "https://www.insee.fr/fr/x" → "insee.fr" */
export function hostLabel(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/** For verdicts reused from a published fact-check: the outlet name (« Déjà vérifié par … »). */
export function libraryOutlet(v: Pick<Verdict, "method" | "sources">): string | null {
  if (v.method !== "library") return null;
  const fc = v.sources.find((s) => s.kind === "factcheck" && s.publisher);
  return fc?.publisher ?? null;
}
