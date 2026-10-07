/** 75 → "1:15", 3725 → "1:02:05". */
export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Spoken form for screen readers: "1 min 15 s". */
export function formatTimeLong(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const parts: string[] = [];
  if (h) parts.push(`${h} h`);
  if (m) parts.push(`${m} min`);
  if (sec || parts.length === 0) parts.push(`${sec} s`);
  return parts.join(" ");
}

/** 10211 → "2 h 50", 2700 → "45 min". */
export function formatDuration(seconds: number | null | undefined): string | null {
  if (!seconds || !Number.isFinite(seconds)) return null;
  const total = Math.round(seconds / 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${String(m).padStart(2, "0")}`;
}

const dateFmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const shortDateFmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/**
 * Display a date or a data period. Source dates are not always ISO: "2022-04-20" → "20 avril 2022",
 * "2022-03" → "mars 2022", "2021-Q4" → "T4 2021", "2021-S1" → "S1 2021", "2021" → "2021".
 * Anything else is shown as-is; never throws.
 */
export function formatDate(value: string | null | undefined, short = false): string | null {
  if (!value) return null;
  const v = value.trim();
  try {
    if (/^\d{4}$/.test(v)) return v;
    let m = /^(\d{4})-?Q([1-4])$/i.exec(v) ?? /^Q([1-4])[- ]?(\d{4})$/i.exec(v);
    if (m) return /^Q/i.test(v) ? `T${m[1]} ${m[2]}` : `T${m[2]} ${m[1]}`;
    m = /^(\d{4})-?S([12])$/i.exec(v) ?? /^(\d{4})-?H([12])$/i.exec(v);
    if (m) return `S${m[2]} ${m[1]}`;
    if (/^\d{4}-\d{2}$/.test(v)) {
      const d = new Date(`${v}-01T00:00:00Z`);
      return Number.isNaN(d.getTime())
        ? v
        : new Intl.DateTimeFormat("fr-FR", { month: short ? "short" : "long", year: "numeric", timeZone: "UTC" }).format(d);
    }
    if (/^\d{4}-\d{2}-\d{2}/.test(v)) {
      const d = new Date(v.length === 10 ? `${v}T00:00:00Z` : v);
      if (!Number.isNaN(d.getTime())) return (short ? shortDateFmt : dateFmt).format(d);
    }
  } catch {
    /* fall through */
  }
  return v;
}

/** French plural helper: plural(2, "affirmation") → "2 affirmations". */
export function plural(n: number, singular: string, pluralForm = `${singular}s`): string {
  return `${n.toLocaleString("fr-FR")} ${Math.abs(n) >= 2 ? pluralForm : singular}`;
}

/** Inserts French typographic non-breaking spaces (before : ; ! ? % » and after «). */
export function frenchSpacing(text: string): string {
  return text
    .replace(/«[ \u00A0\u202F]?/g, "«\u00A0")
    .replace(/[ \u00A0\u202F]?»/g, "\u00A0»")
    .replace(/ ([:;!?%€])/g, "\u00A0$1")
    .replace(/(\d) (?=\d{3}\b)/g, "$1\u00A0");
}

/** Typographic apostrophes for display ("l'an" → "l’an"). Display only: data stays verbatim. */
export function typographic(text: string): string {
  return text.replace(/(\p{L})'(?=\p{L})/gu, "$1’");
}

/** Quote display: typographic apostrophes + French spacing. */
export function displayQuote(text: string): string {
  return frenchSpacing(typographic(text.trim()));
}
