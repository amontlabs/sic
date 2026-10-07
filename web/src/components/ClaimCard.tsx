import { memo } from "react";
import type { Claim, Verdict } from "../types";
import { displayQuote, formatTime, formatTimeLong, frenchSpacing } from "../lib/format";
import type { SpeakerInfo } from "../lib/speakers";
import { claimStatus, libraryOutlet, verdictLabel } from "../lib/verdict";
import { IconArrowRight } from "./icons";
import { cx, MethodIcon, NeutralTag, PendingPill, ReviewBadge, SpeakerName, VerdictPill } from "./ui";

interface Props {
  claim: Claim;
  verdict: Verdict | undefined;
  speaker: SpeakerInfo;
  selected?: boolean;
  active?: boolean; // currently playing
  onSelect?: (claim: Claim) => void;
  animate?: boolean;
}

export const ClaimCard = memo(function ClaimCard({ claim, verdict, speaker, selected, active, onSelect, animate }: Props) {
  const status = claimStatus(claim, verdict);
  const v = status.kind === "verdict" ? status.verdict : null;
  const mainSource = v?.sources[0];
  const a11y = [
    `${speaker.name}, à ${formatTimeLong(claim.start)}`,
    v ? `verdict : ${verdictLabel(v.label)}` : status.kind === "pending" ? "vérification en cours" : "non vérifiable",
  ].join(", ");

  return (
    <button
      type="button"
      onClick={() => onSelect?.(claim)}
      aria-label={`${a11y}. ${claim.quote}`}
      aria-current={selected ? "true" : undefined}
      data-testid="claim-card"
      data-claim-id={claim.id}
      data-verdict={v?.label ?? status.kind}
      className={cx(
        "group relative block w-full overflow-hidden rounded-xl border bg-surface p-4 text-left transition-[border-color,box-shadow] sm:p-[18px]",
        selected ? "border-ink shadow-[0_0_0_1px_var(--ink)]" : "border-line hover:border-line-strong hover:shadow-sm",
        animate && "animate-card-in",
      )}
    >
      <div className="flex items-center justify-between gap-3 text-[13px]">
        <div className="flex min-w-0 items-center gap-2">
          <SpeakerName speaker={speaker} />
          <span className="shrink-0 tabular-nums text-subtle">{formatTime(claim.start)}</span>
          {active && (
            <span className="shrink-0 rounded bg-hl px-1.5 text-[11px] font-semibold text-ink" aria-label="En cours de lecture">
              En cours
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {v && <MethodIcon method={v.method} />}
          {v && <ReviewBadge verdict={v} />}
        </div>
      </div>

      <p className="quote mt-2.5 text-[17px] leading-[1.45] text-ink/90 line-clamp-3">
        « {displayQuote(claim.quote)} »
      </p>

      <div className="mt-3 flex flex-col items-start gap-2">
        {status.kind === "verdict" && (
          <>
            <VerdictPill label={status.verdict.label} />
            <p className="text-[15px] font-semibold leading-snug text-ink">{frenchSpacing(status.verdict.headline)}</p>
          </>
        )}
        {status.kind === "pending" && (
          <>
            <PendingPill />
            <span className="shimmer block h-3.5 w-3/4 rounded" aria-hidden />
          </>
        )}
        {status.kind === "not_checkable" && <NeutralTag>{status.tag}</NeutralTag>}
      </div>

      {v && (
        <div className="mt-3 flex items-center justify-between gap-3 border-t border-line pt-2.5 text-[12.5px] text-muted">
          <span className="truncate">
            {libraryOutlet(v) ? (
              <>Déjà vérifié par {libraryOutlet(v)}</>
            ) : (
              <>
                {mainSource?.publisher ?? mainSource?.title ?? "Aucune source"}
                {v.sources.length > 1 && ` + ${v.sources.length - 1}`}
              </>
            )}
          </span>
          <span className="inline-flex shrink-0 items-center gap-1 font-medium text-ink/80 group-hover:text-ink">
            Sources
            <IconArrowRight className="size-3.5" />
          </span>
        </div>
      )}
    </button>
  );
});
