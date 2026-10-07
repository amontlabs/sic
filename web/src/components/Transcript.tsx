import { memo, useEffect, useMemo, useRef, type KeyboardEvent, type ReactNode } from "react";
import type { Claim, Segment, Verdict } from "../types";
import { formatTime, formatTimeLong } from "../lib/format";
import { speakerOf, type SpeakerMap } from "../lib/speakers";
import { claimsBySegment, findQuoteRange, groupTurns, type Turn } from "../lib/transcript";
import { verdictLabel } from "../lib/verdict";
import { IconFollow } from "./icons";
import { cx } from "./ui";

interface Props {
  segments: Segment[];
  claims: Claim[];
  verdicts: Map<string, Verdict>;
  speakers: SpeakerMap;
  activeIndex: number;
  follow: boolean;
  onFollowChange: (follow: boolean) => void;
  onSeek: (t: number) => void;
  onSelectClaim: (claim: Claim) => void;
  selectedClaimId?: string | null;
  /** re-run auto-scroll when the panel becomes visible (mobile tabs) */
  visible?: boolean;
}

export function Transcript({
  segments,
  claims,
  verdicts,
  speakers,
  activeIndex,
  follow,
  onFollowChange,
  onSeek,
  onSelectClaim,
  selectedClaimId,
  visible,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const turns = useMemo(() => groupTurns(segments), [segments]);
  const bySeg = useMemo(() => claimsBySegment(segments, claims), [segments, claims]);

  // Auto-scroll the active segment into the upper third of the panel (inside the panel only).
  useEffect(() => {
    if (!follow || activeIndex < 0) return;
    const box = scroller.current;
    const el = box?.querySelector<HTMLElement>(`[data-seg-index="${activeIndex}"]`);
    if (!box || !el) return;
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    const target = Math.max(0, top - box.clientHeight * 0.3);
    const distance = Math.abs(box.scrollTop - target);
    // Smooth for nearby sentences; jump instantly after a seek far away (long debates = huge lists).
    if (distance > 4) box.scrollTo({ top: target, behavior: distance > box.clientHeight * 2 ? "auto" : "smooth" });
  }, [activeIndex, follow, visible]);

  const stopFollowing = () => {
    if (follow) onFollowChange(false);
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5 sm:px-5">
        <h2 className="hidden text-[13px] font-semibold uppercase tracking-[0.08em] text-muted lg:block">Transcription</h2>
        <label className="ml-auto inline-flex cursor-pointer select-none items-center gap-2 text-[13px] text-muted">
          <span>Suivre la lecture</span>
          <input
            type="checkbox"
            role="switch"
            checked={follow}
            onChange={(e) => onFollowChange(e.target.checked)}
            className="peer sr-only"
            data-testid="follow-toggle"
          />
          <span
            className="relative h-5 w-9 rounded-full bg-line-strong transition-colors peer-checked:bg-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-(--focus) after:absolute after:left-0.5 after:top-0.5 after:size-4 after:rounded-full after:bg-surface after:shadow after:transition-transform peer-checked:after:translate-x-4"
            aria-hidden
          />
        </label>
      </div>
      <div
        ref={scroller}
        className="thin-scroll min-h-0 flex-1 overflow-y-auto px-4 pb-24 pt-3 sm:px-5"
        onWheel={stopFollowing}
        onTouchMove={stopFollowing}
        onKeyDown={(e) => {
          if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(e.key)) stopFollowing();
        }}
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) stopFollowing(); // scrollbar drag
        }}
        data-testid="transcript"
        aria-label="Transcription synchronisée"
      >
        {turns.length === 0 && <p className="py-10 text-center text-sm text-muted">La transcription apparaîtra ici.</p>}
        {turns.map((turn) => {
          const inTurn = activeIndex >= 0 && turn.segmentIndices.includes(activeIndex);
          return (
            <TurnBlock
              key={turn.id}
              turn={turn}
              segments={segments}
              activeIndex={inTurn ? activeIndex : -1}
              bySeg={bySeg}
              verdicts={verdicts}
              speakers={speakers}
              onSeek={onSeek}
              onSelectClaim={onSelectClaim}
              selectedClaimId={selectedClaimId ?? null}
            />
          );
        })}
      </div>
      {!follow && activeIndex >= 0 && (
        <button
          type="button"
          onClick={() => onFollowChange(true)}
          className="absolute bottom-4 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-ink px-4 py-2 text-[13px] font-semibold text-bg shadow-lg"
        >
          <IconFollow className="size-4" />
          Revenir à la lecture
        </button>
      )}
    </div>
  );
}

interface TurnProps {
  turn: Turn;
  segments: Segment[];
  activeIndex: number;
  bySeg: Map<string, Claim[]>;
  verdicts: Map<string, Verdict>;
  speakers: SpeakerMap;
  onSeek: (t: number) => void;
  onSelectClaim: (c: Claim) => void;
  selectedClaimId: string | null;
}

const TurnBlock = memo(function TurnBlock({
  turn,
  segments,
  activeIndex,
  bySeg,
  verdicts,
  speakers,
  onSeek,
  onSelectClaim,
  selectedClaimId,
}: TurnProps) {
  const speaker = speakerOf(speakers, turn.speaker);
  return (
    <section className="relative my-2 border-l-2 py-1 pl-4" style={{ borderColor: speaker.color }} data-testid="turn">
      <header className="mb-1 flex items-baseline gap-2 text-[13px]">
        <span className="font-semibold" style={{ color: "var(--ink)" }}>
          {speaker.name}
        </span>
        <button
          type="button"
          onClick={() => onSeek(turn.start)}
          className="tabular-nums text-subtle hover:text-ink hover:underline"
          aria-label={`Aller à ${formatTimeLong(turn.start)}`}
        >
          {formatTime(turn.start)}
        </button>
      </header>
      <p className="text-[15.5px] leading-[1.7] text-ink/85">
        {turn.segmentIndices.map((i) => {
          const seg = segments[i]!;
          const segClaims = bySeg.get(seg.id) ?? [];
          const isActive = i === activeIndex;
          const onKey = (e: KeyboardEvent) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onSeek(seg.start);
            }
          };
          return (
            <span key={seg.id}>
              <span
                role="button"
                tabIndex={0}
                data-seg-index={i}
                data-segment-id={seg.id}
                data-active={isActive || undefined}
                aria-current={isActive ? "true" : undefined}
                onClick={() => onSeek(seg.start)}
                onKeyDown={onKey}
                title={`Aller à ${formatTime(seg.start)}`}
                className={cx(
                  "cursor-pointer rounded-[3px] box-decoration-clone transition-colors",
                  isActive ? "bg-hl text-ink" : "hover:bg-surface-2",
                )}
              >
                {highlight(seg.text, segClaims, verdicts)}
              </span>
              {segClaims
                .filter((c) => c.segment_ids.length === 0 || c.segment_ids[c.segment_ids.length - 1] === seg.id)
                .map((c) => {
                  const v = verdicts.get(c.id);
                  const key = v ? v.label : c.checkable ? "pending" : "none";
                  const text = v ? verdictLabel(v.label) : c.checkable ? "En cours" : "Non vérifiable";
                  return (
                    <button
                      key={c.id}
                      type="button"
                      data-verdict={key}
                      data-testid="transcript-claim-chip"
                      onClick={() => onSelectClaim(c)}
                      aria-label={`Voir la vérification : ${text}`}
                      className={cx(
                        "mx-1 inline-flex translate-y-[-1px] items-center gap-1 rounded-full px-2 py-px align-middle text-[11.5px] font-semibold leading-5",
                        v ? "bg-v-bg text-v-fg" : "border border-line-strong text-muted",
                        selectedClaimId === c.id && "ring-2 ring-ink",
                      )}
                    >
                      {v && <span className="size-1.5 rounded-full bg-v-solid" aria-hidden />}
                      {text}
                    </button>
                  );
                })}{" "}
            </span>
          );
        })}
      </p>
    </section>
  );
});

/** Wraps claim quotes found in the segment text in <mark>s underlined with the verdict color. */
function highlight(text: string, claims: Claim[], verdicts: Map<string, Verdict>): ReactNode {
  if (claims.length === 0) return text;
  const ranges: { start: number; end: number; claim: Claim }[] = [];
  for (const c of claims) {
    const r = findQuoteRange(text, c.quote);
    if (r) ranges.push({ start: r[0], end: r[1], claim: c });
  }
  ranges.sort((a, b) => a.start - b.start);
  const out: ReactNode[] = [];
  let pos = 0;
  for (const r of ranges) {
    if (r.start < pos) continue; // overlapping quotes: keep the first
    if (r.start > pos) out.push(text.slice(pos, r.start));
    const v = verdicts.get(r.claim.id);
    out.push(
      <mark
        key={r.claim.id}
        data-verdict={v ? v.label : r.claim.checkable ? "pending" : "none"}
        data-testid="claim-mark"
        className="claim-mark bg-transparent text-inherit"
      >
        {text.slice(r.start, r.end)}
      </mark>,
    );
    pos = r.end;
  }
  if (pos < text.length) out.push(text.slice(pos));
  return out;
}
