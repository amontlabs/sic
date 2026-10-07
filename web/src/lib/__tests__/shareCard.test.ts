import { describe, expect, it } from "vitest";
import { CARD_H, clampLines, fitText, layoutShareCard, shareFileName, typographic, wrapText, type Measure, type ShareCardInput } from "../shareCard";

// Deterministic fake: every character is `size * 0.5` wide.
const sizeOf = (font: string) => Number(/(\d+)px/.exec(font)?.[1] ?? 10);
const measure: Measure = (s, font) => Array.from(s).length * sizeOf(font) * 0.5;
const mono = (s: string) => Array.from(s).length; // 1 unit per char

describe("wrapText", () => {
  it("wraps greedily on spaces", () => {
    expect(wrapText("aa bb cc dd", 5, mono)).toEqual(["aa bb", "cc dd"]);
    expect(wrapText("aa bb cc", 100, mono)).toEqual(["aa bb cc"]);
    expect(wrapText("", 10, mono)).toEqual([]);
  });

  it("never breaks on French non-breaking spaces", () => {
    const lines = wrapText("le taux est de 7,4\u00A0%\u00A0?", 10, mono);
    for (const l of lines) expect(l.startsWith("%") || l.startsWith("?")).toBe(false);
    expect(lines.at(-1)).toBe("7,4\u00A0%\u00A0?");
  });

  it("hard-breaks a word longer than the line", () => {
    expect(wrapText("anticonstitutionnellement", 10, mono)).toEqual(["anticonsti", "tutionnell", "ement"]);
  });

  it("keeps French accents intact", () => {
    const lines = wrapText("Ça a été démontré à l'Assemblée", 12, mono);
    expect(lines.join(" ")).toBe("Ça a été démontré à l'Assemblée");
  });
});

describe("clampLines / fitText", () => {
  it("adds an ellipsis on the last kept line", () => {
    const out = clampLines(["un deux", "trois quatre", "cinq six"], 2, 12, mono);
    expect(out).toHaveLength(2);
    expect(out[1]!.endsWith("…")).toBe(true);
    expect(mono(out[1]!)).toBeLessThanOrEqual(12);
  });

  it("picks the largest size that fits", () => {
    const text = "x".repeat(10) + " " + "y".repeat(10);
    // at 20px: 10 chars = 100 → fits 2 lines at width 120; at 40px it needs > 2 lines
    const r = fitText(text, [40, 20], 2, 120, (s) => `${s}px X`, measure);
    expect(r.size).toBe(20);
    expect(r.truncated).toBe(false);
  });

  it("truncates at the smallest size when nothing fits", () => {
    const r = fitText("mot ".repeat(200), [30, 20], 3, 200, (s) => `${s}px X`, measure);
    expect(r.size).toBe(20);
    expect(r.lines).toHaveLength(3);
    expect(r.truncated).toBe(true);
  });
});

const input: ShareCardInput = {
  speakerName: "Candidate B",
  speakerColor: "#7048e8",
  debateTitle: "Débat fictif (données de test)",
  debateDate: "2022-04-20",
  time: 24,
  quote: "j'ai toujours voté contre la réforme des retraites.",
  label: "faux",
  headline: "A voté pour en première lecture (scrutin n° 1234)",
  reviewed: true,
  sourcePublisher: "Assemblée nationale",
  sourceDate: "2020-03-03",
};

describe("layoutShareCard", () => {
  const texts = (l: ReturnType<typeof layoutShareCard>) => l.ops.flatMap((o) => (o.kind === "text" ? [o.text] : []));

  it("contains every required element", () => {
    const l = layoutShareCard(input, measure);
    const all = texts(l).join("\n");
    expect(all).toContain("Candidate B");
    expect(all).toContain("Faux");
    expect(all).toContain("sic.vote");
    expect(all).toContain("Assemblée nationale");
    expect(all).toContain("à 0:24");
    expect(all).toContain("Relu par un humain");
    expect(all).toContain("j’ai"); // typographic apostrophe
    expect(all).toMatch(/^«\u00A0/m);
  });

  it("keeps long content above the footer by shrinking/clamping", () => {
    const long = layoutShareCard({ ...input, quote: "une phrase très longue ".repeat(40), headline: "titre ".repeat(60) }, measure);
    expect(long.quote.lines.length).toBeLessThanOrEqual(6);
    expect(long.headline.lines.length).toBeLessThanOrEqual(4);
    expect(long.contentBottom).toBeLessThan(CARD_H - 88 - 70);
    const short = layoutShareCard(input, measure);
    expect(short.quote.size).toBeGreaterThan(long.quote.size);
  });

  it("omits the source line when there is none and says Auto otherwise", () => {
    const l = layoutShareCard({ ...input, sourcePublisher: null, reviewed: false }, measure);
    expect(texts(l).some((t) => t.startsWith("Source"))).toBe(false);
    expect(texts(l)).toContain("Vérification automatique");
  });

  it("typographic apostrophes and safe filenames", () => {
    expect(typographic("l'an dernier, aujourd'hui")).toBe("l’an dernier, aujourd’hui");
    expect(shareFileName({ speakerName: "Élodie Côté", time: 125.7 })).toBe("sic-elodie-cote-125s.png");
  });
});
