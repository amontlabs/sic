import { useEffect, useRef, useState } from "react";
import type { Claim, Debate, Verdict } from "../types";
import { displayQuote, formatDate, formatTime, frenchSpacing } from "../lib/format";
import type { SpeakerInfo } from "../lib/speakers";
import { claimStatus, hostLabel, isHomepageUrl, libraryOutlet, METHOD_LABELS, reviewLabel, SOURCE_KIND_LABELS } from "../lib/verdict";
import { IconArrowLeft, IconBook, IconClose, IconExternal, IconFlag, IconPlay, IconShare } from "./icons";
import { OperatorReview } from "./OperatorReview";
import { ShareDialog } from "./ShareDialog";
import { Button, buttonClass, cx, MethodIcon, NeutralTag, PendingPill, ReviewBadge, SpeakerName, VerdictPill } from "./ui";

interface Props {
  debate: Debate;
  claim: Claim;
  verdict: Verdict | undefined;
  speaker: SpeakerInfo;
  operator: boolean;
  onClose: () => void;
  onPlayExcerpt: (t: number) => void;
  onVerdictUpdated: (v: Verdict) => void;
  /** "back" inside the claims column, "close" inside a modal sheet */
  closeStyle?: "back" | "close";
}

export const REPORT_EMAIL = "corrections@sic.vote";

export function ClaimDetail({
  debate,
  claim,
  verdict,
  speaker,
  operator,
  onClose,
  onPlayExcerpt,
  onVerdictUpdated,
  closeStyle = "back",
}: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
  const [sharing, setSharing] = useState(false);
  const status = claimStatus(claim, verdict);
  const v = status.kind === "verdict" ? status.verdict : null;

  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [claim.id]);

  const reportHref = `mailto:${REPORT_EMAIL}?${new URLSearchParams({
    subject: `Signalement · ${debate.title} · ${claim.id}`,
    body: `Vérification concernée : « ${claim.quote} » (${speaker.name}, ${formatTime(claim.start)})\nVerdict affiché : ${v ? v.label : "aucun"}\n\nCe qui me semble erroné :\n\nSource éventuelle :\n`,
  })
    .toString()
    .replace(/\+/g, "%20")}`;

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-labelledby="claim-detail-title"
      data-testid="claim-detail"
      className="flex h-full min-h-0 flex-col bg-surface"
      onKeyDown={(e) => {
        if (e.key === "Escape" && !sharing) onClose();
      }}
    >
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 sm:px-4">
        {closeStyle === "back" && (
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Fermer et revenir à la liste">
            <IconArrowLeft className="size-4" />
            Retour
          </Button>
        )}
        <h2
          id="claim-detail-title"
          ref={heading}
          tabIndex={-1}
          className={cx(
            "text-[13px] font-semibold uppercase tracking-[0.08em] text-muted outline-none",
            closeStyle === "back" ? "ml-1" : "ml-2 flex-1",
          )}
        >
          Vérification
        </h2>
        {closeStyle === "close" && (
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-muted hover:bg-surface-2 hover:text-ink"
            aria-label="Fermer"
          >
            <IconClose className="size-5" />
          </button>
        )}
      </div>

      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto px-5 pb-10 pt-5 sm:px-6">
        <div className="flex items-center gap-2 text-[13.5px]">
          <SpeakerName speaker={speaker} />
          <span className="tabular-nums text-subtle">à {formatTime(claim.start)}</span>
          <span className="text-subtle">·</span>
          <span className="truncate text-subtle">{claim.topic}</span>
        </div>

        <blockquote className="quote mt-3 text-[22px] leading-[1.4] text-ink sm:text-[24px]">
          « {displayQuote(claim.quote)} »
        </blockquote>

        <div className="mt-4 rounded-lg bg-surface-2 px-3.5 py-2.5 text-[14px] leading-relaxed text-muted">
          <span className="font-semibold text-ink">Affirmation vérifiée : </span>
          {frenchSpacing(claim.claim)}
        </div>

        <section className="mt-6" aria-label="Verdict">
          {status.kind === "verdict" && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <VerdictPill label={status.verdict.label} size="lg" />
                <ReviewBadge verdict={status.verdict} />
                {libraryOutlet(status.verdict) && (
                  <span className="inline-flex items-center gap-1 rounded-md border border-line-strong px-1.5 py-0.5 text-[12px] font-medium text-muted">
                    <IconBook className="size-3.5" />
                    Déjà vérifié par {libraryOutlet(status.verdict)}
                  </span>
                )}
              </div>
              <p className="mt-3 text-[19px] font-semibold leading-snug text-ink">{frenchSpacing(status.verdict.headline)}</p>
              <p className="mt-2 text-[15px] leading-relaxed text-ink/80">{frenchSpacing(status.verdict.explanation)}</p>
              <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-subtle">
                <MethodIcon method={status.verdict.method} withLabel />
                <span>{reviewLabel(status.verdict).long}</span>
              </p>
            </>
          )}
          {status.kind === "pending" && <PendingPill />}
          {status.kind === "not_checkable" && (
            <div className="flex flex-col items-start gap-2">
              <NeutralTag>{status.tag}</NeutralTag>
              <p className="text-[14px] text-muted">
                Cette phrase n'énonce pas un fait vérifiable (engagement, opinion ou prévision). Elle est affichée sans
                verdict.
              </p>
            </div>
          )}
        </section>

        <div className="mt-5 flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => onPlayExcerpt(claim.start)}>
            <IconPlay className="size-4" />
            Voir l'extrait
          </Button>
          {v && (
            <Button onClick={() => setSharing(true)}>
              <IconShare className="size-4" />
              Partager
            </Button>
          )}
          <a className={buttonClass("ghost")} href={reportHref}>
            <IconFlag className="size-4" />
            Signaler une erreur
          </a>
        </div>

        {v && (
          <section className="mt-7" aria-labelledby="sources-title">
            <h3 id="sources-title" className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
              Sources ({v.sources.length})
            </h3>
            {v.sources.length === 0 && <p className="mt-2 text-[14px] text-muted">Aucune source citée pour ce verdict.</p>}
            <ol className="mt-3 flex flex-col gap-3" data-testid="sources">
              {v.sources.map((s, i) => (
                <li key={`${s.url ?? s.title}-${i}`} className="rounded-xl border border-line p-4">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12.5px] text-subtle">
                    <span className="font-semibold text-ink">{s.publisher ?? "Source"}</span>
                    {s.date && <span>· {formatDate(s.date, true)}</span>}
                    <span className="rounded bg-surface-2 px-1.5 py-px text-[11.5px]">{SOURCE_KIND_LABELS[s.kind]}</span>
                  </div>
                  <p className="mt-1.5 text-[15px] font-medium leading-snug text-ink">{s.title}</p>
                  {s.excerpt && (
                    <p className="quote mt-2 border-l-2 border-line-strong pl-3 text-[15px] italic text-ink/75">
                      {displayQuote(s.excerpt)}
                    </p>
                  )}
                  {s.url && (
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-2.5 inline-flex items-center gap-1 text-[13.5px] font-medium text-accent underline-offset-2 hover:underline"
                    >
                      {isHomepageUrl(s.url) ? `Site de l’éditeur (${hostLabel(s.url)})` : "Consulter la source"}
                      <span className="sr-only"> (nouvel onglet)</span>
                      <IconExternal className="size-3.5" />
                    </a>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}

        {operator && status.kind !== "not_checkable" && (
          <OperatorReview key={claim.id} debateId={debate.id} claim={claim} verdict={v} onUpdated={onVerdictUpdated} />
        )}

        <p className={cx("mt-8 border-t border-line pt-4 text-[12.5px] leading-relaxed text-subtle")}>
          Même méthode pour tous. Chaque verdict cite au moins une source. Méthodes : {METHOD_LABELS.library.toLowerCase()},{" "}
          {METHOD_LABELS.data.toLowerCase()}, {METHOD_LABELS.web.toLowerCase()}.
        </p>
      </div>

      {sharing && v && (
        <ShareDialog
          onClose={() => setSharing(false)}
          input={{
            speakerName: speaker.name,
            speakerColor: speaker.color,
            debateTitle: debate.title,
            debateDate: debate.date,
            time: claim.start,
            quote: claim.quote,
            label: v.label,
            headline: v.headline,
            reviewed: v.reviewed,
            sourcePublisher: v.sources[0]?.publisher ?? v.sources[0]?.title ?? null,
            sourceDate: v.sources[0]?.date ?? null,
          }}
        />
      )}
    </div>
  );
}
