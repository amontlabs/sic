import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import type { Claim, DebateSummary, Run, Verdict } from "../types";
import { api, ApiError } from "../lib/api";
import { filterClaims, filterFromParams, filterToParams, type ClaimFilter } from "../lib/filters";
import { formatDate, plural } from "../lib/format";
import { buildSpeakers, speakerOf, type SpeakerInfo } from "../lib/speakers";
import { findActiveIndex } from "../lib/sync";
import { countByLabel, isNotable } from "../lib/verdict";
import { ClaimCard } from "../components/ClaimCard";
import { ClaimDetail } from "../components/ClaimDetail";
import { EqualTreatment } from "../components/EqualTreatment";
import { FilterBar } from "../components/FilterBar";
import { IconLive, IconSpinner } from "../components/icons";
import { Transcript } from "../components/Transcript";
import { cx, Logo } from "../components/ui";
import { MediaPlayer, sourceForDebate } from "../player/MediaPlayer";
import type { PlayerApi } from "../player/types";
import { NotFound } from "./NotFound";

type Tab = "claims" | "transcript" | "bilan";

export function Replay() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const operator = params.get("operator") === "1";
  const [run, setRun] = useState<Run | null>(null);
  const [summary, setSummary] = useState<DebateSummary | null>(null);
  const [error, setError] = useState<{ status: number; message: string } | null>(null);
  const [overrides, setOverrides] = useState<Map<string, Verdict>>(new Map());

  // ---- data loading (polls while the debate is still being processed) ----
  useEffect(() => {
    const ctrl = new AbortController();
    let timer = 0;
    setRun(null);
    setError(null);
    const load = async () => {
      try {
        const [r, list] = await Promise.all([
          api.getRun(id, { events: false, signal: ctrl.signal }),
          api.listDebates(ctrl.signal).catch(() => [] as DebateSummary[]),
        ]);
        const s = list.find((d) => d.id === id) ?? null;
        setRun(r);
        setSummary(s);
        if (s?.status === "processing") timer = window.setTimeout(load, 4000);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        if (e instanceof ApiError && e.status === 404) {
          // a freshly submitted analysis may not be listed yet: retry a few times
          setError({ status: 404, message: "Débat introuvable." });
          timer = window.setTimeout(load, 3000);
        } else {
          setError({ status: e instanceof ApiError ? e.status : 0, message: "Impossible de charger le débat." });
          timer = window.setTimeout(load, 5000);
        }
      }
    };
    void load();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [id]);

  if (!run) {
    if (error?.status === 404) return <NotFound message="Ce débat est introuvable (ou son analyse n'a pas encore commencé)." />;
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 text-muted" aria-busy="true">
        <IconSpinner className="size-6" />
        <p className="text-[14px]">{error ? `${error.message} Nouvelle tentative…` : "Chargement du débat…"}</p>
      </div>
    );
  }
  return (
    <ReplayView
      run={run}
      summary={summary}
      operator={operator}
      params={params}
      setParams={setParams}
      overrides={overrides}
      setOverrides={setOverrides}
    />
  );
}

interface ViewProps {
  run: Run;
  summary: DebateSummary | null;
  operator: boolean;
  params: URLSearchParams;
  setParams: ReturnType<typeof useSearchParams>[1];
  overrides: Map<string, Verdict>;
  setOverrides: (m: Map<string, Verdict>) => void;
}

function ReplayView({ run, summary, operator, params, setParams, overrides, setOverrides }: ViewProps) {
  const { debate, segments } = run;
  const playerRef = useRef<PlayerApi | null>(null);
  const [time, setTime] = useState(0);
  const [follow, setFollow] = useState(true);
  const [tab, setTab] = useState<Tab>("claims");
  const pendingSeek = useRef<number | null>(null);

  useEffect(() => {
    document.title = `${debate.title} — Sic`;
  }, [debate.title]);

  const claims = useMemo(() => [...run.claims].sort((a, b) => a.start - b.start), [run.claims]);
  const verdicts = useMemo(() => {
    const m = new Map<string, Verdict>();
    for (const v of run.verdicts) m.set(v.claim_id, v);
    for (const [k, v] of overrides) m.set(k, v);
    return m;
  }, [run.verdicts, overrides]);
  const speakers = useMemo(
    () => buildSpeakers(debate.roster, [...segments.map((s) => s.speaker), ...claims.map((c) => c.speaker)]),
    [debate.roster, segments, claims],
  );
  const claimSpeakers = useMemo(() => {
    const ids = new Set(claims.map((c) => c.speaker));
    return [...speakers.values()].filter((s) => s.role === "candidate" || (s.role !== "moderator" && ids.has(s.id)));
  }, [speakers, claims]);

  const duration = debate.media.duration ?? segments[segments.length - 1]?.end ?? 0;
  const source = useMemo(() => sourceForDebate(debate, duration), [debate, duration]);
  const activeIndex = useMemo(() => findActiveIndex(segments, time), [segments, time]);

  // ---- filters & selection live in the URL ----
  const filter = filterFromParams(params);
  const selectedId = params.get("c");
  const selected = selectedId ? claims.find((c) => c.id === selectedId) ?? null : null;
  const update = useCallback(
    (fn: (p: URLSearchParams) => URLSearchParams | void) => {
      setParams(
        (prev) => {
          const p = new URLSearchParams(prev);
          return fn(p) ?? p;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const setFilter = (f: ClaimFilter) => update((p) => filterToParams(f, p));

  const items = useMemo(() => claims.map((claim) => ({ claim, verdict: verdicts.get(claim.id) })), [claims, verdicts]);
  const visible = filterClaims(items, filter);
  const counts = useMemo(
    () => ({
      notables: items.filter((i) => (i.verdict ? isNotable(i.verdict.label) : i.claim.checkable)).length,
      all: items.length,
      byLabel: countByLabel(items.flatMap((i) => (i.verdict ? [i.verdict] : []))),
    }),
    [items],
  );

  // ---- player wiring ----
  const seek = useCallback((t: number) => {
    const p = playerRef.current;
    if (p) p.seek(t);
    else pendingSeek.current = t;
    setTime(t);
    setFollow(true);
  }, []);
  const onReady = useCallback((api: PlayerApi) => {
    playerRef.current = api;
    if (pendingSeek.current !== null) {
      api.seek(pendingSeek.current);
      pendingSeek.current = null;
    }
  }, []);

  // deep link: ?t=123 or ?c=claim → initial position
  const didInit = useRef(false);
  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    const t = Number(params.get("t"));
    if (Number.isFinite(t) && t > 0) seek(t);
    else if (selected) seek(selected.start);
  }, [params, selected, seek]);

  const selectClaim = useCallback(
    (c: Claim) => {
      seek(c.start);
      setTab("claims");
      update((p) => {
        p.set("c", c.id);
      });
    },
    [seek, update],
  );
  const closeDetail = useCallback(
    () =>
      update((p) => {
        p.delete("c");
      }),
    [update],
  );
  const playExcerpt = useCallback(
    (t: number) => {
      seek(Math.max(0, t - 0.5));
      playerRef.current?.play();
    },
    [seek],
  );

  const status = summary?.status ?? "ready";
  const meta = [formatDate(debate.date), debate.broadcaster].filter(Boolean).join(" · ");

  return (
    <div className="flex h-dvh flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line bg-bg px-4 sm:px-5">
        <Logo size="sm" />
        <span className="h-6 w-px bg-line-strong" aria-hidden />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[14.5px] font-semibold leading-tight sm:text-[15px]">{debate.title}</h1>
          <p className="truncate text-[12.5px] text-muted">{meta}</p>
        </div>
        {status === "ready" && (
          <Link
            to={`/live/${encodeURIComponent(debate.id)}`}
            className="hidden shrink-0 items-center gap-1.5 rounded-full border border-line-strong px-3 py-1.5 text-[13px] font-medium text-ink hover:bg-surface-2 sm:inline-flex"
          >
            <IconLive className="size-4" />
            Direct simulé
          </Link>
        )}
        {operator && (
          <span className="shrink-0 rounded-md bg-ink px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-bg">
            Opérateur
          </span>
        )}
      </header>

      {status === "processing" && (
        <div className="flex shrink-0 items-center gap-3 border-b border-line bg-surface-2 px-4 py-2 text-[13px] text-muted sm:px-5" role="status">
          <IconSpinner className="size-4" />
          <span>
            Analyse en cours · {Math.round((summary?.progress ?? 0) * 100)} % — les résultats apparaissent au fur et à mesure.
          </span>
        </div>
      )}

      <main className="flex min-h-0 flex-1 flex-col lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(380px,32vw)] xl:grid-cols-[minmax(0,1fr)_480px]">
        {/* Left: video + transcript */}
        <div className={cx("flex min-h-0 flex-col lg:border-r lg:border-line", tab === "transcript" ? "flex-1" : "shrink-0", "lg:flex-1")}>
          <div className="aspect-video w-full shrink-0 bg-black lg:aspect-auto lg:h-[min(52vh,calc((100vw-min(32vw,480px))*0.5625))]">
            <MediaPlayer
              source={source}
              title={`Vidéo : ${debate.title}`}
              onReady={onReady}
              onTimeUpdate={setTime}
            />
          </div>

          <MobileTabs tab={tab} setTab={setTab} counts={{ claims: claims.length }} />

          <div className={cx("min-h-0 flex-1 flex-col bg-bg", tab === "transcript" ? "flex" : "hidden", "lg:flex")}>
            <Transcript
              segments={segments}
              claims={claims}
              verdicts={verdicts}
              speakers={speakers}
              activeIndex={activeIndex}
              follow={follow}
              onFollowChange={setFollow}
              onSeek={seek}
              onSelectClaim={selectClaim}
              selectedClaimId={selectedId}
              visible={tab === "transcript"}
            />
          </div>
        </div>

        {/* Right: claims / detail / summary */}
        <aside
          className={cx("relative min-h-0 flex-1 flex-col bg-bg", tab === "transcript" ? "hidden" : "flex", "lg:flex")}
          aria-label="Vérifications"
        >
          {selected ? (
            <ClaimDetail
              debate={debate}
              claim={selected}
              verdict={verdicts.get(selected.id)}
              speaker={speakerOf(speakers, selected.speaker)}
              operator={operator}
              onClose={closeDetail}
              onPlayExcerpt={playExcerpt}
              onVerdictUpdated={(v) => setOverrides(new Map(overrides).set(v.claim_id, v))}
            />
          ) : (
            <div className="thin-scroll min-h-0 flex-1 overflow-y-auto">
              <div className={cx("border-b border-line px-4 py-5 sm:px-5", tab === "bilan" ? "block" : "hidden", "lg:block")}>
                <EqualTreatment speakers={claimSpeakers} claims={claims} verdicts={verdicts} compact />
              </div>
              <div className={cx(tab === "bilan" ? "hidden" : "block", "lg:block")}>
                <div className="sticky top-0 z-10 border-b border-line bg-bg/95 px-4 pb-3 pt-4 backdrop-blur sm:px-5">
                  <div className="mb-3 hidden items-baseline justify-between lg:flex">
                    <h2 className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">Vérifications</h2>
                    <span className="text-[12.5px] text-subtle" aria-live="polite">
                      {plural(visible.length, "affirmation")}
                    </span>
                  </div>
                  <FilterBar filter={filter} onChange={setFilter} speakers={claimSpeakers} counts={counts} />
                </div>
                <ClaimList
                  items={visible}
                  speakers={speakers}
                  time={time}
                  selectedId={selectedId}
                  onSelect={selectClaim}
                  emptyAll={claims.length === 0}
                  onShowAll={() => setFilter({ scope: "all", speaker: null, label: null })}
                />
              </div>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

function MobileTabs({ tab, setTab, counts }: { tab: Tab; setTab: (t: Tab) => void; counts: { claims: number } }) {
  const tabs: { id: Tab; label: string }[] = [
    { id: "claims", label: `Vérifications (${counts.claims})` },
    { id: "transcript", label: "Transcription" },
    { id: "bilan", label: "Bilan" },
  ];
  return (
    <div role="tablist" aria-label="Sections" className="flex shrink-0 border-b border-line bg-bg px-2 lg:hidden">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          aria-selected={tab === t.id}
          onClick={() => setTab(t.id)}
          className={cx(
            "relative h-11 flex-1 px-2 text-[13.5px] font-medium transition-colors",
            tab === t.id ? "text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-ink" : "text-muted",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

function ClaimList({
  items,
  speakers,
  time,
  selectedId,
  onSelect,
  emptyAll,
  onShowAll,
}: {
  items: { claim: Claim; verdict: Verdict | undefined }[];
  speakers: Map<string, SpeakerInfo>;
  time: number;
  selectedId: string | null;
  onSelect: (c: Claim) => void;
  emptyAll: boolean;
  onShowAll: () => void;
}) {
  if (items.length === 0) {
    return (
      <div className="px-5 py-12 text-center text-[14px] text-muted">
        {emptyAll ? (
          "Aucune affirmation vérifiable détectée pour l'instant."
        ) : (
          <>
            Aucune vérification ne correspond à ce filtre.{" "}
            <button type="button" className="font-medium text-ink underline underline-offset-2" onClick={onShowAll}>
              Tout afficher
            </button>
          </>
        )}
      </div>
    );
  }
  return (
    <ul className="flex flex-col gap-3 px-4 py-4 sm:px-5" data-testid="claim-list">
      {items.map(({ claim, verdict }) => (
        <li key={claim.id}>
          <ClaimCard
            claim={claim}
            verdict={verdict}
            speaker={speakerOf(speakers, claim.speaker)}
            selected={claim.id === selectedId}
            active={time >= claim.start && time < claim.end}
            onSelect={onSelect}
          />
        </li>
      ))}
    </ul>
  );
}
