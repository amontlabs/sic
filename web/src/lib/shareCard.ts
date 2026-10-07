import type { VerdictLabel } from "../types";
import { formatDate, formatTime, frenchSpacing, typographic } from "./format";

export { typographic };
import { VERDICT_HEX, verdictLabel } from "./verdict";

/** Share card: 1080×1350 (4:5, the portrait format most social feeds accept). Rendered client-side. */
export const CARD_W = 1080;
export const CARD_H = 1350;
const PAD = 88;
const INNER_W = CARD_W - PAD * 2;

export const SANS = '"Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif';
export const SERIF = '"Newsreader Variable", Newsreader, Georgia, "Times New Roman", serif';

const INK = "#16181d";
const MUTED = "#5b616b";
const PAPER = "#fbfaf7";
const RULE = "#e3e0d8";

export interface ShareCardInput {
  speakerName: string;
  speakerColor: string;
  debateTitle: string;
  debateDate: string | null;
  time: number; // seconds into the debate
  quote: string;
  label: VerdictLabel;
  headline: string;
  reviewed: boolean;
  sourcePublisher: string | null;
  sourceDate: string | null;
}

export type Measure = (text: string, font: string) => number;

/**
 * Greedy word wrap. Breaks only on regular spaces so French non-breaking spaces
 * (before « : ; ! ? % » ») keep punctuation attached. Over-long words are hard-broken.
 */
export function wrapText(text: string, maxWidth: number, measure: (s: string) => number): string[] {
  const words = text.split(/ +/).filter((w) => w.length > 0);
  const lines: string[] = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (measure(candidate) <= maxWidth) {
      line = candidate;
      continue;
    }
    if (line) lines.push(line);
    if (measure(word) <= maxWidth) {
      line = word;
      continue;
    }
    // hard-break a word wider than the line
    let chunk = "";
    for (const ch of Array.from(word)) {
      if (measure(chunk + ch) > maxWidth && chunk) {
        lines.push(chunk);
        chunk = ch;
      } else chunk += ch;
    }
    line = chunk;
  }
  if (line) lines.push(line);
  return lines;
}

/** Truncate the last allowed line with an ellipsis so it fits. */
export function clampLines(lines: string[], maxLines: number, maxWidth: number, measure: (s: string) => number): string[] {
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  let last = kept[maxLines - 1]!;
  while (last.length > 0 && measure(`${last}…`) > maxWidth) last = last.slice(0, -1).trimEnd();
  kept[maxLines - 1] = `${last.replace(/[\s,;:.]+$/, "")}…`;
  return kept;
}

export interface FitResult {
  size: number;
  lines: string[];
  truncated: boolean;
}

/** Largest font size (from `sizes`, descending) whose wrapped text fits `maxLines`. */
export function fitText(
  text: string,
  sizes: readonly number[],
  maxLines: number,
  maxWidth: number,
  fontAt: (size: number) => string,
  measure: Measure,
): FitResult {
  for (const size of sizes) {
    const font = fontAt(size);
    const lines = wrapText(text, maxWidth, (s) => measure(s, font));
    if (lines.length <= maxLines) return { size, lines, truncated: false };
  }
  const size = sizes[sizes.length - 1]!;
  const font = fontAt(size);
  const m = (s: string) => measure(s, font);
  return { size, lines: clampLines(wrapText(text, maxWidth, m), maxLines, maxWidth, m), truncated: true };
}

export type DrawOp =
  | { kind: "rect"; x: number; y: number; w: number; h: number; r: number; fill: string; stroke?: string }
  | { kind: "circle"; x: number; y: number; r: number; fill: string }
  | { kind: "text"; text: string; x: number; y: number; font: string; color: string; align?: "left" | "right" };

export interface CardLayout {
  ops: DrawOp[];
  quote: FitResult;
  headline: FitResult;
  /** bottom of the content stack, must stay above the footer rule */
  contentBottom: number;
}

const sans = (weight: number, size: number) => `${weight} ${size}px ${SANS}`;
const serif = (weight: number, size: number) => `${weight} ${size}px ${SERIF}`;

/** Pure layout: produces draw operations (unit-tested with a fake `measure`). */
export function layoutShareCard(input: ShareCardInput, measure: Measure): CardLayout {
  const ops: DrawOp[] = [];
  const text = (list: DrawOp[], t: string, x: number, y: number, font: string, color: string, align?: "left" | "right") =>
    list.push({ kind: "text", text: t, x, y, font, color, align });

  ops.push({ kind: "rect", x: 0, y: 0, w: CARD_W, h: CARD_H, r: 0, fill: PAPER });
  ops.push({ kind: "rect", x: 0, y: 0, w: CARD_W, h: 14, r: 0, fill: VERDICT_HEX[input.label].solid });

  // Header: wordmark + context
  const headerY = PAD + 40;
  text(ops, "[sic]", PAD, headerY, serif(600, 52), INK);
  const ctxLine = ["Vérification", formatDate(input.debateDate)].filter(Boolean).join(" · ");
  text(ops, ctxLine, CARD_W - PAD, headerY - 6, sans(500, 28), MUTED, "right");
  const headerBottom = headerY + 36;
  ops.push({ kind: "rect", x: PAD, y: headerBottom, w: INNER_W, h: 2, r: 0, fill: RULE });

  // Footer
  const fy = CARD_H - PAD;
  const footerTop = fy - 70;
  ops.push({ kind: "rect", x: PAD, y: footerTop, w: INNER_W, h: 2, r: 0, fill: RULE });
  text(ops, "sic.vote", PAD, fy, sans(700, 36), INK);
  text(ops, "Même méthode pour tous. Sources citées.", CARD_W - PAD, fy, sans(450, 26), MUTED, "right");

  // Content block, laid out from y=0 then centred between header and footer
  const c: DrawOp[] = [];
  let y = 40;
  c.push({ kind: "circle", x: PAD + 13, y: y - 14, r: 13, fill: input.speakerColor });
  const nameFont = sans(650, 40);
  text(c, input.speakerName, PAD + 42, y, nameFont, INK);
  const nameW = measure(input.speakerName, nameFont);
  text(c, `à ${formatTime(input.time)}`, PAD + 42 + nameW + 18, y, sans(450, 32), MUTED);
  y += 48;
  const titleFit = fitText(input.debateTitle, [28], 1, INNER_W, (s) => sans(450, s), measure);
  text(c, titleFit.lines[0] ?? "", PAD, y, sans(450, 28), MUTED);

  const quoteText = frenchSpacing(typographic(`« ${input.quote.trim()} »`));
  const quote = fitText(quoteText, [84, 76, 68, 60, 54, 48], 6, INNER_W, (s) => serif(450, s), measure);
  y += 40;
  const qLine = Math.round(quote.size * 1.2);
  for (const line of quote.lines) {
    y += qLine;
    text(c, line, PAD, y, serif(450, quote.size), INK);
  }

  y += 60;
  const pillFont = sans(700, 44);
  const label = verdictLabel(input.label);
  const pillW = measure(label, pillFont) + 64;
  const pillH = 84;
  const hex = VERDICT_HEX[input.label];
  c.push({ kind: "rect", x: PAD, y, w: pillW, h: pillH, r: pillH / 2, fill: hex.solid });
  text(c, label, PAD + 32, y + 57, pillFont, "#ffffff");
  const badge = input.reviewed ? "Relu par un humain" : "Vérification automatique";
  const badgeFont = sans(550, 28);
  const badgeW = measure(badge, badgeFont) + 44;
  c.push({ kind: "rect", x: PAD + pillW + 20, y: y + 14, w: badgeW, h: 56, r: 28, fill: PAPER, stroke: "#c9c5bb" });
  text(c, badge, PAD + pillW + 20 + 22, y + 52, badgeFont, MUTED);
  y += pillH;

  const headline = fitText(frenchSpacing(typographic(input.headline)), [48, 44, 40, 36], 4, INNER_W, (s) => sans(600, s), measure);
  y += 22;
  const hLine = Math.round(headline.size * 1.28);
  for (const line of headline.lines) {
    y += hLine;
    text(c, line, PAD, y, sans(600, headline.size), INK);
  }

  if (input.sourcePublisher) {
    y += 70;
    const src = ["Source : " + input.sourcePublisher, formatDate(input.sourceDate, true)].filter(Boolean).join(", ");
    const srcFit = fitText(frenchSpacing(src), [32], 1, INNER_W, (s) => sans(500, s), measure);
    text(c, srcFit.lines[0] ?? "", PAD, y, sans(500, 32), MUTED);
  }
  const contentH = y + 16;
  const avail = footerTop - headerBottom;
  const offset = headerBottom + Math.max(24, Math.round((avail - contentH) * 0.42));
  for (const op of c) ops.push({ ...op, y: op.y + offset });

  return { ops, quote, headline, contentBottom: offset + y };
}

export function drawOps(ctx: CanvasRenderingContext2D, ops: readonly DrawOp[]): void {
  ctx.textBaseline = "alphabetic";
  for (const op of ops) {
    if (op.kind === "rect") {
      ctx.beginPath();
      if (op.r > 0) ctx.roundRect(op.x, op.y, op.w, op.h, op.r);
      else ctx.rect(op.x, op.y, op.w, op.h);
      ctx.fillStyle = op.fill;
      ctx.fill();
      if (op.stroke) {
        ctx.strokeStyle = op.stroke;
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    } else if (op.kind === "circle") {
      ctx.beginPath();
      ctx.arc(op.x, op.y, op.r, 0, Math.PI * 2);
      ctx.fillStyle = op.fill;
      ctx.fill();
    } else {
      ctx.font = op.font;
      ctx.fillStyle = op.color;
      ctx.textAlign = op.align ?? "left";
      ctx.fillText(op.text, op.x, op.y);
    }
  }
}

async function ensureFonts(): Promise<void> {
  if (typeof document === "undefined" || !document.fonts) return;
  const sample = "Àéèêëîïôöùûüç«»";
  await Promise.allSettled([
    document.fonts.load(`450 64px "Newsreader Variable"`, sample),
    document.fonts.load(`600 52px "Newsreader Variable"`, sample),
    document.fonts.load(`400 32px "Inter Variable"`, sample),
    document.fonts.load(`700 44px "Inter Variable"`, sample),
  ]);
}

export async function renderShareCard(input: ShareCardInput): Promise<Blob> {
  await ensureFonts();
  const canvas = document.createElement("canvas");
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas indisponible");
  const measure: Measure = (s, font) => {
    ctx.font = font;
    return ctx.measureText(s).width;
  };
  drawOps(ctx, layoutShareCard(input, measure).ops);
  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Export PNG impossible"))), "image/png"),
  );
}

export function shareFileName(input: Pick<ShareCardInput, "speakerName" | "time">): string {
  const slug = input.speakerName
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `sic-${slug || "verification"}-${Math.floor(input.time)}s.png`;
}

/** Web Share API with files when available, otherwise a download. Returns what happened. */
export async function shareOrDownload(blob: Blob, filename: string, text: string): Promise<"shared" | "downloaded" | "cancelled"> {
  const file = new File([blob], filename, { type: "image/png" });
  const nav = navigator as Navigator & { canShare?: (d: ShareData) => boolean };
  if (nav.share && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file], text, title: "Sic — vérification" });
      return "shared";
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return "cancelled";
      // fall through to download
    }
  }
  downloadBlob(blob, filename);
  return "downloaded";
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
