import type { VerdictLabel } from "../types";
import type { ClaimFilter } from "../lib/filters";
import type { SpeakerInfo } from "../lib/speakers";
import { VERDICT_ORDER, verdictLabel } from "../lib/verdict";
import { Chip } from "./ui";

interface Props {
  filter: ClaimFilter;
  onChange: (f: ClaimFilter) => void;
  speakers: SpeakerInfo[];
  counts?: { notables: number; all: number; byLabel?: Partial<Record<VerdictLabel, number>> };
  showLabels?: boolean;
}

export function FilterBar({ filter, onChange, speakers, counts, showLabels = true }: Props) {
  const scopeActive = (s: ClaimFilter["scope"]) => !filter.label && filter.scope === s;
  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Filtrer les vérifications">
      <div className="pill-scroll -mx-1 flex items-center gap-1.5 overflow-x-auto px-1 py-0.5">
        <Chip
          active={scopeActive("notables")}
          onClick={() => onChange({ ...filter, scope: "notables", label: null })}
          title="Imprécis, trompeur ou faux"
        >
          Notables
          {counts && <Count n={counts.notables} active={scopeActive("notables")} />}
        </Chip>
        <Chip active={scopeActive("all")} onClick={() => onChange({ ...filter, scope: "all", label: null })}>
          Tout
          {counts && <Count n={counts.all} active={scopeActive("all")} />}
        </Chip>
        {speakers.length > 0 && <span className="mx-1 h-5 w-px shrink-0 bg-line-strong" aria-hidden />}
        {speakers.map((s) => {
          const active = filter.speaker === s.id;
          return (
            <Chip
              key={s.id}
              active={active}
              onClick={() => onChange({ ...filter, speaker: active ? null : s.id })}
              aria-label={`Seulement ${s.name}`}
            >
              <span className="size-2 rounded-full" style={{ background: s.color }} aria-hidden />
              {s.name}
            </Chip>
          );
        })}
      </div>
      {showLabels && (
        <div className="pill-scroll -mx-1 flex items-center gap-1.5 overflow-x-auto px-1 py-0.5 lg:flex-wrap lg:overflow-visible" aria-label="Par verdict">
          {VERDICT_ORDER.map((l) => {
            const active = filter.label === l;
            const n = counts?.byLabel?.[l] ?? 0;
            return (
              <Chip
                key={l}
                active={active}
                data-verdict={l}
                onClick={() => onChange({ ...filter, label: active ? null : l })}
                className="h-7 px-2.5 text-[12.5px]"
                disabled={counts?.byLabel !== undefined && n === 0 && !active}
              >
                <span className="size-2 rounded-full bg-v-solid" aria-hidden />
                {verdictLabel(l)}
                {counts?.byLabel && <Count n={n} active={active} />}
              </Chip>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Count({ n, active }: { n: number; active: boolean }) {
  return <span className={active ? "tabular-nums text-bg/70" : "tabular-nums text-subtle"}>{n}</span>;
}
