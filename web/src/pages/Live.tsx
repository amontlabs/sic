import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import type { Claim, Run } from "../types";
import { api, ApiError } from "../lib/api";
import { DEFAULT_FILTER, filterClaims, type ClaimFilter } from "../lib/filters";
import { formatTime, frenchSpacing } from "../lib/format";
import { deriveLiveView } from "../lib/live";
import { buildSpeakers, speakerOf } from "../lib/speakers";
import { clamp } from "../lib/sync";
import { useLiveStream } from "../lib/useLiveStream";
import { isNotable } from "../lib/verdict";
import { ClaimCard } from "../components/ClaimCard";
import { ClaimDetail } from "../components/ClaimDetail";
import { FilterBar } from "../components/FilterBar";
import { IconSpinner, IconTv } from "../components/icons";
import { cx, Logo } from "../components/ui";
import { MediaPlayer, sourceForDebate } from "../player/MediaPlayer";
import type { PlayerApi } from "../player/types";
import { NotFound } from "./NotFound";

export const SPEEDS = [1, 2, 4, 10] as const;
export const DELAY_MIN = -30;
export const DELAY_MAX = 30;

export function Live() {
  const { id = "" } = useParams();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<number | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api
      .getRun(id, { events: false, signal: ctrl.signal })
      .then(setRun)
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(e instanceof ApiError ? e.status : 0);
      });
    return () => ctrl.abort();
  }, [id]);

  if (error === 404) return <NotFound message="Ce débat est introuvable." />;
  if (!run)
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-3 text-muted" aria-busy="true">
        <IconSpinner className="size-6" />
        <p className="text-[14px]">{error !== null ? "Le serveur Sic est injoignable." : "Connexion au débat…"}</p>
      </div>
    );
  return <LiveView run={run} />;
}

function LiveView({ run }: { run: Run }) {
  const { debate } = run;
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const from = Math.max(0, Number(params.get("from")) || 0);
  const speedParam = Number(params.get("speed"));
  const speed = (SPEEDS as readonly number[]).includes(speedParam) ? speedParam : 1;
  const [delay, setDelay] = useState(() => clamp(Number(params.get("delay")) || 0, DELAY_MIN, DELAY_MAX));
  const [simTv, setSimTv] = useState(params.get("tv") === "1");
  const [filter, setFilter] = useState<ClaimFilter>(DEFAULT_FILTER);
  const [openClaim, setOpenClaim] = useState<Claim | null>(null);

  useEffect(() => {
    document.title = `En direct · ${debate.title} — Sic`;
  }, [debate.title]);

  const { state, clock } = useLiveStream(debate.id, from, speed);
  const view = useMemo(() => deriveLiveView(state, clock, delay), [state, clock, delay]);
  const speakerMap = useMemo(() => {
    const m = buildSpeakers(debate.roster);
    for (const [cluster, p] of Object.entries(state.people)) {
      const known = m.get(p.value.id);
      m.set(cluster, known ? { ...known, id: cluster } : { ...speakerOf(m, cluster), name: p.value.name });
    }
    return m;
  }, [debate.roster, state.people]);
  const candidates = useMemo(() => [...speakerMap.values()].filter((s) => s.role === "candidate"), [speakerMap]);

  const visible = filterClaims(view.items, filter);
  const counts = {
    notables: view.items.filter((i) => (i.verdict ? isNotable(i.verdict.label) : i.claim.checkable)).length,
    all: view.items.length,
  };

  const setSpeed = (s: number) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("speed", String(s));
        p.set("from", String(Math.floor(clock)));
        return p;
      },
      { replace: true },
    );

  const tvTime = clock - delay;
  const lastSeg = view.lastSegment;
  const lastSpeaker = lastSeg ? speakerOf(speakerMap, lastSeg.speaker) : null;

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-3 px-4">
          <Logo size="sm" />
          <LivePill state={state.connection} />
        </div>
      </header>

      <div className={cx("mx-auto max-w-6xl px-4", simTv && "lg:grid lg:grid-cols-[minmax(0,1fr)_440px] lg:gap-8")}>
        {simTv && (
          <div className="sticky top-14 z-10 -mx-4 bg-bg pb-3 pt-0 lg:top-14 lg:mx-0 lg:self-start lg:pt-5">
            <SimulatedTv run={run} target={tvTime} speed={speed} ended={state.ended} />
            <p className="mt-2 hidden text-[12.5px] text-subtle lg:block">
              Simulation de votre télévision (démo) : la vidéo suit le direct
              {delay !== 0 ? ` avec ${Math.abs(delay)} s ${delay > 0 ? "de retard" : "d’avance"}` : ""}.
            </p>
          </div>
        )}

        <main
          className={cx("mx-auto w-full pb-28", !simTv && "max-w-xl")}
          data-testid="live-main"
          data-stream-clock={clock.toFixed(1)}
        >
          <section className="pt-5" aria-label="Contexte">
            <h1 className="text-[13px] font-medium text-muted">{debate.title}</h1>
            <p className="mt-1 font-serif text-[22px] font-medium leading-tight" data-testid="live-topic">
              {view.topic ? <>Sujet : {capitalize(view.topic)}</> : "Le débat va commencer"}
            </p>
          </section>

          {/* ticker: last transcribed sentence */}
          <section
            className="mt-4 rounded-xl border border-line bg-surface px-4 py-3"
            aria-label="Dernière phrase transcrite"
            aria-live="off"
            data-testid="live-ticker"
          >
            <div className="flex items-center justify-between text-[12px] text-subtle">
              <span className="font-semibold uppercase tracking-[0.08em]">Transcription</span>
              <span className="tabular-nums" data-testid="live-clock">
                {formatTime(Math.max(0, tvTime))}
              </span>
            </div>
            {lastSeg && lastSpeaker ? (
              <p className="mt-1.5 line-clamp-2 text-[14.5px] leading-snug text-ink/85">
                <span className="font-semibold" style={{ color: "var(--ink)" }}>
                  <span className="mr-1.5 inline-block size-2 rounded-full align-middle" style={{ background: lastSpeaker.color }} aria-hidden />
                  {lastSpeaker.name} :
                </span>{" "}
                {frenchSpacing(lastSeg.text)}
              </p>
            ) : (
              <p className="mt-1.5 text-[14px] text-muted">En attente des premières paroles…</p>
            )}
          </section>

          <LiveControls
            delay={delay}
            setDelay={setDelay}
            speed={speed}
            setSpeed={setSpeed}
            simTv={simTv}
            setSimTv={setSimTv}
            pending={view.pending}
          />

          <div className="sticky top-14 z-10 -mx-4 mt-4 bg-bg/95 px-4 py-2.5 backdrop-blur">
            <FilterBar filter={filter} onChange={setFilter} speakers={candidates} counts={counts} showLabels={false} />
          </div>

          <ul className="mt-2 flex flex-col gap-3" aria-live="polite" aria-relevant="additions" data-testid="live-feed">
            {visible.map(({ claim, verdict }) => (
              <li key={claim.id}>
                <ClaimCard
                  claim={claim}
                  verdict={verdict}
                  speaker={speakerOf(speakerMap, claim.speaker)}
                  onSelect={setOpenClaim}
                  animate
                />
              </li>
            ))}
          </ul>
          {visible.length === 0 && (
            <p className="mt-10 text-center text-[14px] leading-relaxed text-muted">
              {view.items.length === 0
                ? state.ended
                  ? "Aucune affirmation vérifiable dans ce passage."
                  : "Les vérifications apparaîtront ici dès qu'une affirmation sera détectée."
                : "Rien de notable pour l'instant."}
            </p>
          )}
          {state.ended && view.pending === 0 && (
            <p className="mt-8 text-center text-[13px] text-subtle" data-testid="live-ended">
              Fin du direct.
            </p>
          )}
        </main>
      </div>

      {openClaim && (
        <ClaimSheet onClose={() => setOpenClaim(null)}>
          <ClaimDetail
            debate={debate}
            claim={openClaim}
            verdict={view.items.find((i) => i.claim.id === openClaim.id)?.verdict}
            speaker={speakerOf(speakerMap, openClaim.speaker)}
            operator={false}
            closeStyle="close"
            onClose={() => setOpenClaim(null)}
            onPlayExcerpt={(t) => navigate(`/d/${encodeURIComponent(debate.id)}?c=${encodeURIComponent(openClaim.id)}&t=${Math.floor(t)}`)}
            onVerdictUpdated={() => undefined}
          />
        </ClaimSheet>
      )}
    </div>
  );
}

function capitalize(s: string): string {
  return s ? s[0]!.toLocaleUpperCase("fr-FR") + s.slice(1) : s;
}

function LivePill({ state }: { state: string }) {
  const label =
    state === "ended" ? "Débat · Terminé" : state === "reconnecting" ? "Reconnexion…" : state === "open" ? "Débat · En direct" : "Connexion…";
  const live = state === "open";
  return (
    <span
      className={cx(
        "inline-flex items-center gap-2 rounded-full px-3 py-1 text-[12.5px] font-semibold",
        live ? "bg-ink text-bg" : "border border-line-strong text-muted",
      )}
      role="status"
      data-testid="live-status"
      data-state={state}
    >
      <span className={cx("size-2 rounded-full", live ? "animate-live bg-[#e5484d]" : "bg-subtle")} aria-hidden />
      {label}
    </span>
  );
}

function LiveControls({
  delay,
  setDelay,
  speed,
  setSpeed,
  simTv,
  setSimTv,
  pending,
}: {
  delay: number;
  setDelay: (d: number) => void;
  speed: number;
  setSpeed: (s: number) => void;
  simTv: boolean;
  setSimTv: (b: boolean) => void;
  pending: number;
}) {
  const delayText = delay === 0 ? "0 s" : `${delay > 0 ? "+" : "−"}${Math.abs(delay)} s`;
  return (
    <section className="mt-3 rounded-xl border border-line bg-surface px-4 py-3" aria-label="Réglages du direct">
      <div className="flex items-center gap-3">
        <label htmlFor="tv-delay" className="shrink-0 text-[13px] font-semibold" title="Votre télé est en retard ? Augmentez le décalage pour que les vérifications arrivent au bon moment.">
          Décalage TV
        </label>
        <input
          id="tv-delay"
          type="range"
          min={DELAY_MIN}
          max={DELAY_MAX}
          step={1}
          value={delay}
          onChange={(e) => setDelay(Number(e.target.value))}
          aria-valuetext={delay === 0 ? "Aucun décalage" : `${delay} secondes`}
          aria-describedby="tv-delay-help"
          className="h-5 min-w-0 flex-1 accent-(--ink)"
          data-testid="delay-slider"
        />
        <span className="w-11 shrink-0 text-right text-[13px] font-semibold tabular-nums" data-testid="delay-value">
          {delayText}
        </span>
      </div>
      <p id="tv-delay-help" className="mt-1 text-[12px] leading-snug text-subtle">
        {pending > 0 && delay > 0
          ? `${pending} élément${pending > 1 ? "s" : ""} en attente pour rester synchronisé avec votre télé.`
          : "Votre télé est en retard ? Augmentez le décalage."}
      </p>

      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-line pt-2.5">
        <div className="flex items-center gap-2" role="group" aria-label="Vitesse de simulation">
          <span className="text-[12.5px] text-muted">Vitesse</span>
          <div className="inline-flex rounded-lg bg-surface-2 p-0.5">
            {SPEEDS.map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={speed === s}
                onClick={() => setSpeed(s)}
                className={cx(
                  "h-6 min-w-8 rounded-md px-1.5 text-[12px] font-semibold tabular-nums",
                  speed === s ? "bg-surface text-ink shadow-sm ring-1 ring-line-strong" : "text-muted hover:text-ink",
                )}
              >
                {s}×
              </button>
            ))}
          </div>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 text-[12.5px] text-muted">
          <IconTv className="size-4" />
          Simuler la TV
          <input
            type="checkbox"
            role="switch"
            checked={simTv}
            onChange={(e) => setSimTv(e.target.checked)}
            className="peer sr-only"
            data-testid="sim-tv"
          />
          <span
            className="relative h-5 w-9 rounded-full bg-line-strong transition-colors peer-checked:bg-ink peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-(--focus) after:absolute after:left-0.5 after:top-0.5 after:size-4 after:rounded-full after:bg-surface after:shadow after:transition-transform peer-checked:after:translate-x-4"
            aria-hidden
          />
        </label>
      </div>
    </section>
  );
}

/** Dev/demo: plays the debate video in sync with the stream clock minus the TV delay. */
function SimulatedTv({ run, target, speed, ended }: { run: Run; target: number; speed: number; ended: boolean }) {
  const player = useRef<PlayerApi | null>(null);
  const [ready, setReady] = useState(false);
  const duration = run.debate.media.duration ?? run.segments[run.segments.length - 1]?.end ?? 0;
  const source = useMemo(() => sourceForDebate(run.debate, duration), [run.debate, duration]);

  useEffect(() => {
    const p = player.current;
    if (!p || !ready) return;
    if (target < 0 || (ended && target >= duration)) {
      if (!p.isPaused()) p.pause();
      if (target < 0) p.seek(0);
      return;
    }
    p.setPlaybackRate(speed);
    const drift = Math.abs(p.getCurrentTime() - target);
    if (drift > Math.max(1, speed * 0.6)) p.seek(target);
    if (p.isPaused()) p.play();
  }, [target, speed, ready, ended, duration]);

  const onReady = useCallback((api: PlayerApi) => {
    player.current = api;
    setReady(true);
  }, []);

  return (
    <div className="overflow-hidden bg-black lg:rounded-xl" data-testid="sim-tv-player">
      <div className="aspect-video w-full">
        <MediaPlayer source={source} title="Simulation de la télévision" muted controls={false} onReady={onReady} />
      </div>
    </div>
  );
}

function ClaimSheet({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) ref.current?.close();
      }}
      className="mb-0 mt-auto h-[88dvh] max-h-none w-full max-w-none overflow-hidden rounded-t-2xl border border-line bg-surface p-0 text-ink shadow-2xl backdrop:bg-black/50 sm:m-auto sm:h-[min(86dvh,820px)] sm:w-[560px] sm:rounded-2xl"
      aria-label="Détail de la vérification"
    >
      {children}
    </dialog>
  );
}
