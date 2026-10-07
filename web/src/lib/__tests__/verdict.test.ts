import { describe, expect, it } from "vitest";
import { VERDICT_LABELS_FR } from "../../types";
import { formatDate, formatDuration, formatTime, formatTimeLong, frenchSpacing, plural } from "../format";
import {
  claimStatus,
  countByLabel,
  hostLabel,
  isHomepageUrl,
  isNotable,
  libraryOutlet,
  nonCheckableTag,
  reviewLabel,
  VERDICT_ORDER,
  verdictLabel,
} from "../verdict";
import { loadFixture } from "../../test/fixture";

describe("verdict formatting", () => {
  it("uses the French labels from the models", () => {
    expect(VERDICT_ORDER.map(verdictLabel)).toEqual(["Exact", "Plutôt exact", "Imprécis", "Trompeur", "Faux", "Invérifiable"]);
    expect(VERDICT_LABELS_FR.plutot_exact).toBe("Plutôt exact");
  });

  it("never uses the word « mensonge »", () => {
    const all = [...VERDICT_ORDER.map(verdictLabel), reviewLabel({ reviewed: true }).long, reviewLabel({ reviewed: false }).long];
    for (const s of all) expect(s.toLowerCase()).not.toContain("mensonge");
  });

  it("flags notable verdicts", () => {
    expect(VERDICT_ORDER.filter(isNotable)).toEqual(["imprecis", "trompeur", "faux"]);
  });

  it("labels human review", () => {
    expect(reviewLabel({ reviewed: true }).short).toBe("Relu");
    expect(reviewLabel({ reviewed: false }).short).toBe("Auto");
  });

  it("describes non-checkable claims neutrally", () => {
    const run = loadFixture();
    const promise = run.claims.find((c) => !c.checkable)!;
    expect(nonCheckableTag(promise)).toBe("Promesse · non vérifiable");
    expect(nonCheckableTag({ quote: "Les Français méritent mieux.", claim: "Opinion.", kind: "autre" })).toBe("Opinion · non vérifiable");
    expect(claimStatus(promise, undefined).kind).toBe("not_checkable");
    expect(claimStatus(run.claims[0]!, undefined).kind).toBe("pending");
    expect(claimStatus(run.claims[0]!, run.verdicts[0]).kind).toBe("verdict");
  });

  it("counts by label with every label present", () => {
    const run = loadFixture();
    expect(countByLabel(run.verdicts)).toEqual({ exact: 2, plutot_exact: 1, imprecis: 1, trompeur: 0, faux: 2, inverifiable: 0 });
  });
});

describe("format helpers", () => {
  it("formats media times", () => {
    expect(formatTime(0)).toBe("0:00");
    expect(formatTime(75.9)).toBe("1:15");
    expect(formatTime(3725)).toBe("1:02:05");
    expect(formatTime(-3)).toBe("0:00");
    expect(formatTime(Number.NaN)).toBe("0:00");
    expect(formatTimeLong(75)).toBe("1 min 15 s");
    expect(formatTimeLong(0)).toBe("0 s");
  });

  it("formats durations and dates in French", () => {
    expect(formatDuration(10211.5)).toBe("2 h 50");
    expect(formatDuration(2700)).toBe("45 min");
    expect(formatDuration(null)).toBeNull();
    expect(formatDate("2022-04-20")).toBe("20 avril 2022");
    expect(formatDate("2021")).toBe("2021");
    expect(formatDate("2021-03")).toBe("mars 2021");
    expect(formatDate(null)).toBeNull();
    // data periods from connectors: never crash, prettify when possible
    expect(formatDate("2021-Q4")).toBe("T4 2021");
    expect(formatDate("Q2 2020")).toBe("T2 2020");
    expect(formatDate("2022-S1")).toBe("S1 2022");
    expect(formatDate("2022-03", true)).toBe("mars 2022");
    expect(formatDate("fin 2021")).toBe("fin 2021");
    expect(formatDate("12")).toBe("12");
    expect(formatDate("2022-02-18T10:00:00Z", true)).toBe("18 févr. 2022");
  });

  it("pluralises", () => {
    expect(plural(0, "verdict")).toBe("0 verdict");
    expect(plural(1, "verdict")).toBe("1 verdict");
    expect(plural(2, "verdict")).toBe("2 verdicts");
  });

  it("inserts French non-breaking spaces", () => {
    const N = " ";
    expect(frenchSpacing("« Vraiment ? » 7,4 %")).toBe(`«${N}Vraiment${N}?${N}» 7,4${N}%`);
    expect(frenchSpacing("Source : INSEE")).toBe(`Source${N}: INSEE`);
    expect(frenchSpacing("«Bonjour»")).toBe(`«${N}Bonjour${N}»`);
    expect(frenchSpacing("1 200 000 emplois")).toBe(`1${N}200${N}000 emplois`);
  });
});

describe("source helpers", () => {
  it("detects bare homepage links", () => {
    expect(isHomepageUrl("https://www.insee.fr")).toBe(true);
    expect(isHomepageUrl("https://www.insee.fr/")).toBe(true);
    expect(isHomepageUrl("https://www.insee.fr/fr/statistiques/6050298")).toBe(false);
    expect(isHomepageUrl("pas une url")).toBe(false);
    expect(isHomepageUrl(null)).toBe(false);
    expect(hostLabel("https://www.insee.fr/fr/x")).toBe("insee.fr");
  });

  it("names the outlet for verdicts reused from published fact-checks", () => {
    const v = loadFixture().verdicts[0]!;
    expect(libraryOutlet(v)).toBeNull(); // library verdict without a factcheck source
    const fc = { ...v, sources: [{ kind: "factcheck" as const, title: "Vrai ou faux", url: null, publisher: "Libération", date: "2022-03", excerpt: null }] };
    expect(libraryOutlet(fc)).toBe("Libération");
    expect(libraryOutlet({ ...fc, method: "web" })).toBeNull();
  });
});
