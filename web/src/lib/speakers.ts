import type { Person } from "../types";

/**
 * Speaker palette: hues chosen away from the verdict colors (green/amber/orange/red/gray)
 * so a speaker color is never mistaken for a verdict. Same weight & saturation for everyone.
 */
export const SPEAKER_PALETTE = ["#3b5bdb", "#7048e8", "#0b7285", "#a33ea1", "#1864ab", "#5f3dc4"] as const;
export const NEUTRAL_SPEAKER = "#6b7280";

export interface SpeakerInfo {
  id: string;
  name: string;
  role: Person["role"] | "unknown";
  party: string | null;
  color: string;
  /** index among candidates/guests, -1 for moderators/unknown */
  index: number;
}

export type SpeakerMap = Map<string, SpeakerInfo>;

/** Stable assignment: roster order, moderators in neutral gray, unknown clusters appended. */
export function buildSpeakers(roster: readonly Person[], extraIds: Iterable<string | null> = []): SpeakerMap {
  const map: SpeakerMap = new Map();
  let i = 0;
  for (const p of roster) {
    const isMod = p.role === "moderator";
    map.set(p.id, {
      id: p.id,
      name: p.name,
      role: p.role,
      party: p.party,
      color: isMod ? NEUTRAL_SPEAKER : SPEAKER_PALETTE[i % SPEAKER_PALETTE.length]!,
      index: isMod ? -1 : i++,
    });
  }
  let unknown = 1;
  for (const id of extraIds) {
    if (!id || map.has(id)) continue;
    map.set(id, { id, name: `Intervenant ${unknown++}`, role: "unknown", party: null, color: NEUTRAL_SPEAKER, index: -1 });
  }
  return map;
}

export function speakerOf(map: SpeakerMap, id: string | null | undefined): SpeakerInfo {
  if (id && map.has(id)) return map.get(id)!;
  return { id: id ?? "?", name: id ? id : "Intervenant inconnu", role: "unknown", party: null, color: NEUTRAL_SPEAKER, index: -1 };
}

export function initials(name: string): string {
  const parts = name.replace(/[^\p{L}\s-]/gu, "").split(/[\s-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}
