// Mirror of pipeline/src/sic/models.py — keep in sync.
// Optional-with-default pydantic fields are typed as required here because the server
// always serialises them; nullable fields stay `| null`.

export type VerdictLabel = "exact" | "plutot_exact" | "imprecis" | "trompeur" | "faux" | "inverifiable";
export type ClaimKind = "chiffre" | "vote" | "loi" | "historique" | "comparaison" | "citation" | "autre";
export type SourceKind = "official_data" | "parliament" | "law" | "factcheck" | "library" | "web";
export type Role = "candidate" | "moderator" | "guest";
export type VerdictMethod = "library" | "data" | "web" | "none";

export const VERDICT_LABELS_FR: Record<VerdictLabel, string> = {
  exact: "Exact",
  plutot_exact: "Plutôt exact",
  imprecis: "Imprécis",
  trompeur: "Trompeur",
  faux: "Faux",
  inverifiable: "Invérifiable",
};

export interface Person {
  id: string; // stable slug, e.g. "lepen"
  name: string;
  role: Role;
  party: string | null;
  aliases: string[];
  an_id: string | null;
}

export interface Media {
  youtube_id: string | null;
  file: string | null; // path relative to repo root (dev only)
  duration: number | null;
}

export interface Debate {
  id: string;
  title: string;
  date: string; // ISO date "YYYY-MM-DD"
  broadcaster: string | null;
  roster: Person[];
  media: Media;
  language: string;
}

export interface Word {
  start: number;
  end: number;
  text: string;
  prob: number | null;
}

export interface Segment {
  id: string;
  start: number;
  end: number;
  text: string;
  speaker: string | null; // roster id or anonymous cluster ("spk3")
  words: Word[];
}

export interface Source {
  kind: SourceKind;
  title: string;
  url: string | null;
  publisher: string | null;
  date: string | null;
  excerpt: string | null;
}

export interface Claim {
  id: string;
  speaker: string;
  start: number;
  end: number;
  quote: string;
  claim: string;
  topic: string;
  kind: ClaimKind;
  checkable: boolean;
  priority: number; // 1 = high, 3 = low
  segment_ids: string[];
}

export interface Verdict {
  claim_id: string;
  label: VerdictLabel;
  headline: string;
  explanation: string;
  sources: Source[];
  method: VerdictMethod;
  reviewed: boolean;
  confidence: number;
  library_id: string | null;
  latency_s: number | null;
}

export type EventType = "segment" | "claim" | "verdict" | "speaker" | "topic" | "status";

export interface SicEvent {
  seq: number;
  type: EventType;
  t: number; // media time the event refers to
  at: number; // media time at which the event became available
  data: Record<string, unknown>;
}

export interface Run {
  debate: Debate;
  segments: Segment[];
  claims: Claim[];
  verdicts: Verdict[];
  events?: SicEvent[]; // omitted with ?events=0
  stats: Record<string, unknown>;
}

// ---- API-only shapes (docs/API.md) ----

export type DebateStatus = "ready" | "processing" | "error";

export interface DebateSummary {
  id: string;
  title: string;
  date: string;
  broadcaster: string | null;
  duration: number | null;
  status: DebateStatus;
  progress: number; // 0..1
  counts: {
    claims: number;
    verdicts: number;
    by_label: Partial<Record<VerdictLabel, number>>;
  };
}

export interface AnalyzeResponse {
  debate_id: string;
}

export interface VerdictReview {
  label?: VerdictLabel;
  headline?: string;
  explanation?: string;
  reviewed: true;
}
