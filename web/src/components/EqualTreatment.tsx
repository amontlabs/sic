import type { Claim, Verdict, VerdictLabel } from "../types";
import { plural } from "../lib/format";
import type { SpeakerInfo } from "../lib/speakers";
import { countByLabel, VERDICT_ORDER, verdictLabel } from "../lib/verdict";

interface Props {
  speakers: SpeakerInfo[];
  claims: Claim[];
  verdicts: Map<string, Verdict>;
  compact?: boolean;
}

export interface SpeakerTally {
  speaker: SpeakerInfo;
  total: number;
  counts: Record<VerdictLabel, number>;
  notCheckable: number;
}

export function tallyBySpeaker(speakers: SpeakerInfo[], claims: Claim[], verdicts: Map<string, Verdict>): SpeakerTally[] {
  return speakers.map((speaker) => {
    const own = claims.filter((c) => c.speaker === speaker.id);
    const vs = own.map((c) => verdicts.get(c.id)).filter((v): v is Verdict => !!v);
    return {
      speaker,
      total: vs.length,
      counts: countByLabel(vs),
      notCheckable: own.filter((c) => !c.checkable).length,
    };
  });
}

/** Per-candidate verdict distribution. Same scale, same order, same colors for everyone. */
export function EqualTreatment({ speakers, claims, verdicts, compact }: Props) {
  const tallies = tallyBySpeaker(speakers, claims, verdicts);
  return (
    <section aria-labelledby="bilan-title" data-testid="equal-treatment">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="bilan-title" className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
          Bilan par intervenant
        </h2>
      </div>
      <ul className={compact ? "mt-3 flex flex-col gap-3" : "mt-4 flex flex-col gap-4"}>
        {tallies.map(({ speaker, total, counts }) => (
          <li key={speaker.id}>
            <div className="flex items-baseline justify-between gap-3 text-[14px]">
              <span className="inline-flex min-w-0 items-center gap-1.5 font-semibold">
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: speaker.color }} aria-hidden />
                <span className="truncate">{speaker.name}</span>
              </span>
              <span className="shrink-0 text-[13px] tabular-nums text-muted">{plural(total, "vérification")}</span>
            </div>
            <div
              className="mt-1.5 flex h-2.5 w-full overflow-hidden rounded-full bg-surface-3"
              role="img"
              aria-label={
                total === 0
                  ? `${speaker.name} : aucune affirmation vérifiée`
                  : `${speaker.name} : ` +
                    VERDICT_ORDER.filter((l) => counts[l] > 0)
                      .map((l) => `${counts[l]} ${verdictLabel(l).toLowerCase()}`)
                      .join(", ")
              }
            >
              {total > 0 &&
                VERDICT_ORDER.map((l) =>
                  counts[l] > 0 ? (
                    <span
                      key={l}
                      data-verdict={l}
                      className="h-full bg-v-solid [&+span]:border-l-2 [&+span]:border-surface"
                      style={{ width: `${(counts[l] / total) * 100}%` }}
                    />
                  ) : null,
                )}
            </div>
            {total > 0 && (
              <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-muted" aria-hidden>
                {VERDICT_ORDER.filter((l) => counts[l] > 0).map((l) => (
                  <span key={l} data-verdict={l} className="inline-flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-v-solid" />
                    <span className="tabular-nums font-semibold text-ink">{counts[l]}</span> {verdictLabel(l)}
                  </span>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-4 text-[12.5px] leading-relaxed text-subtle">
        Même méthode pour tous. Chaque verdict cite au moins une source.
      </p>
    </section>
  );
}
