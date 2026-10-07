import { describe, expect, it } from "vitest";
import { claimsBySegment, findQuoteRange, groupTurns } from "../transcript";
import { buildSpeakers, initials, NEUTRAL_SPEAKER, speakerOf } from "../speakers";
import { loadFixture } from "../../test/fixture";

const run = loadFixture();

describe("transcript helpers", () => {
  it("groups consecutive segments into speaker turns", () => {
    const turns = groupTurns(run.segments);
    expect(turns.map((t) => t.speaker)).toEqual([
      "moderatrice",
      "candidat-a",
      "candidate-b",
      "candidat-a",
      "moderatrice",
      "candidate-b",
      "candidat-a",
      "candidate-b",
    ]);
    expect(turns[1]!.segmentIndices).toEqual([1, 2]);
    expect(turns[1]!.end).toBe(20);
  });

  it("attaches claims to segments", () => {
    const map = claimsBySegment(run.segments, run.claims);
    expect(map.get("s0003")?.map((c) => c.id)).toEqual(["c0003"]);
    expect(map.get("s0000")).toBeUndefined();
    // fallback on time overlap when segment_ids is empty
    const c = { ...run.claims[0]!, segment_ids: [] };
    expect(claimsBySegment(run.segments, [c]).get("s0001")?.[0]?.id).toBe("c0001");
  });

  it("finds quotes exactly, loosely, or across segment boundaries", () => {
    const text = "C'est faux, et j'ai toujours voté contre la réforme des retraites.";
    expect(findQuoteRange(text, "j'ai toujours voté contre la réforme des retraites.")).toEqual([15, text.length]);
    const loose = findQuoteRange(text, "J’ai toujours VOTE contre la reforme");
    expect(loose && text.slice(...loose)).toBe("j'ai toujours voté contre la réforme");
    // quote starting in this segment and continuing in the next one
    const span = findQuoteRange("Je pense que la dette a augmenté", "la dette a augmenté de 600 milliards");
    expect(span).toEqual([13, 32]);
    expect(findQuoteRange("Bonjour à tous", "rien à voir ici")).toBeNull();
  });
});

describe("speakers", () => {
  it("assigns stable colors, gray for moderators and unknown clusters", () => {
    const map = buildSpeakers(run.debate.roster, ["spk9", "candidat-a", null]);
    expect(map.get("moderatrice")?.color).toBe(NEUTRAL_SPEAKER);
    expect(map.get("candidat-a")?.color).not.toBe(map.get("candidate-b")?.color);
    expect(map.get("spk9")?.name).toBe("Intervenant 1");
    expect(speakerOf(map, "zzz").color).toBe(NEUTRAL_SPEAKER);
    expect(initials("Marine Le Pen")).toBe("MP");
    expect(initials("Modératrice")).toBe("MO");
  });
});
