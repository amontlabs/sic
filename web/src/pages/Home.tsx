import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import type { DebateSummary, VerdictLabel } from "../types";
import { api, ApiError, parseYouTubeId } from "../lib/api";
import { formatDate, formatDuration, plural } from "../lib/format";
import { VERDICT_ORDER, verdictLabel } from "../lib/verdict";
import { IconArrowRight, IconLive, IconSpinner } from "../components/icons";
import { buttonClass, cx, Logo } from "../components/ui";

export function Home() {
  const [debates, setDebates] = useState<DebateSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Sic — Ce qu’ils disent, vérifié en direct";
    const ctrl = new AbortController();
    let timer = 0;
    const load = async () => {
      try {
        const list = await api.listDebates(ctrl.signal);
        setDebates(list);
        setError(null);
        if (list.some((d) => d.status === "processing")) timer = window.setTimeout(load, 3000);
      } catch (e) {
        if (ctrl.signal.aborted) return;
        setError(e instanceof ApiError && e.status === 0 ? "Le serveur Sic est injoignable." : "Impossible de charger les débats.");
        timer = window.setTimeout(load, 5000);
      }
    };
    void load();
    return () => {
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, []);

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between px-5">
          <Logo />
          <a href="#methode" className="text-[14px] font-medium text-muted hover:text-ink">
            Méthode
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5">
        <section className="pb-10 pt-12 sm:pb-14 sm:pt-20">
          <p className="text-[13px] font-semibold uppercase tracking-[0.12em] text-muted">Vérification des débats politiques</p>
          <h1 className="mt-3 max-w-3xl text-balance font-serif text-[40px] font-medium leading-[1.05] tracking-[-0.015em] text-ink sm:text-[64px]">
            Ce qu’ils disent, vérifié en direct.
          </h1>
          <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-muted sm:text-[18px]">
            Sic transcrit le débat, repère les affirmations vérifiables et donne un verdict sourcé, en quelques secondes.
            Même méthode pour chaque intervenant.
          </p>
          <AnalyzeForm />
        </section>

        <section aria-labelledby="debats-title" className="pb-16">
          <div className="flex items-baseline justify-between border-b border-ink pb-2">
            <h2 id="debats-title" className="font-serif text-[26px] font-medium">
              Débats
            </h2>
            {debates && <span className="text-[13px] text-muted">{plural(debates.length, "débat")}</span>}
          </div>
          {error && (
            <p role="alert" className="mt-6 rounded-lg border border-line bg-surface p-4 text-[14px] text-muted">
              {error} Nouvelle tentative dans quelques secondes…
            </p>
          )}
          {!debates && !error && (
            <ul className="mt-2" aria-busy="true" aria-label="Chargement des débats">
              {[0, 1, 2].map((i) => (
                <li key={i} className="border-b border-line py-6">
                  <div className="shimmer h-6 w-2/3 rounded" />
                  <div className="shimmer mt-3 h-4 w-1/3 rounded" />
                </li>
              ))}
            </ul>
          )}
          {debates && debates.length === 0 && (
            <p className="mt-6 text-[15px] text-muted">Aucun débat pour l'instant. Collez un lien ci-dessus pour lancer une analyse.</p>
          )}
          {debates && debates.length > 0 && (
            <ul className="mt-1" data-testid="debate-list">
              {debates.map((d) => (
                <DebateRow key={d.id} d={d} />
              ))}
            </ul>
          )}
        </section>

        <section id="methode" aria-labelledby="methode-title" className="mb-20 grid gap-8 border-t border-line pt-10 sm:grid-cols-3">
          <h2 id="methode-title" className="font-serif text-[26px] font-medium sm:col-span-3">
            Méthode
          </h2>
          <MethodItem title="Repérer">
            Chaque phrase est transcrite et attribuée à son auteur. Seules les affirmations factuelles vérifiables sont retenues ;
            promesses et opinions sont signalées comme telles.
          </MethodItem>
          <MethodItem title="Vérifier">
            Chaque verdict s'appuie sur des sources publiques : données officielles, scrutins, textes de loi, vérifications publiées.
            Au moins une source est toujours citée.
          </MethodItem>
          <MethodItem title="Relire">
            Les verdicts automatiques sont marqués « Auto ». Ceux validés par un humain portent la mention « Relu ». Une erreur ?
            Signalez-la depuis chaque vérification.
          </MethodItem>
        </section>
      </main>
      <footer className="border-t border-line py-8 text-center text-[13px] text-subtle">
        sic.vote · Même méthode pour tous.
      </footer>
    </div>
  );
}

function MethodItem({ title, children }: { title: string; children: string }) {
  return (
    <div>
      <h3 className="text-[15px] font-semibold">{title}</h3>
      <p className="mt-2 text-[14.5px] leading-relaxed text-muted">{children}</p>
    </div>
  );
}

function AnalyzeForm() {
  const navigate = useNavigate();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!parseYouTubeId(url)) {
      setError("Collez un lien YouTube valide (youtube.com/watch?v=… ou youtu.be/…).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { debate_id } = await api.analyze(url.trim());
      navigate(`/d/${encodeURIComponent(debate_id)}`);
    } catch (err) {
      setError(err instanceof ApiError ? `L'analyse n'a pas pu démarrer (${err.message}).` : "L'analyse n'a pas pu démarrer.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-8 max-w-2xl" noValidate>
      <label htmlFor="analyze-url" className="text-[14px] font-semibold text-ink">
        Analyser une vidéo
      </label>
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <input
          id="analyze-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setError(null);
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby="analyze-help"
          className="h-12 w-full min-w-0 rounded-xl sm:w-auto sm:flex-1 border border-line-strong bg-surface px-4 text-[15px] text-ink placeholder:text-subtle focus-visible:border-ink"
        />
        <button type="submit" disabled={busy} className={cx(buttonClass("primary"), "h-12 rounded-xl px-5 disabled:opacity-50")}>
          {busy ? <IconSpinner className="size-4" /> : null}
          Analyser une vidéo
        </button>
      </div>
      <p id="analyze-help" className={cx("mt-2 text-[13px]", error ? "text-(--v-faux-fg)" : "text-subtle")} role={error ? "alert" : undefined}>
        {error ?? "Lien YouTube d'un débat. L'analyse prend quelques minutes ; les résultats s'affichent au fur et à mesure."}
      </p>
    </form>
  );
}

function DebateRow({ d }: { d: DebateSummary }) {
  const meta = [formatDate(d.date), d.broadcaster, formatDuration(d.duration)].filter(Boolean).join(" · ");
  return (
    <li className="group relative border-b border-line py-6" data-testid="debate-row" data-debate-id={d.id}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-10">
        <div className="min-w-0 flex-1">
          <h3 className="font-serif text-[22px] font-medium leading-snug sm:text-[24px]">
            <Link
              to={`/d/${encodeURIComponent(d.id)}`}
              className="after:absolute after:inset-0 after:content-[''] group-hover:underline group-hover:decoration-1 group-hover:underline-offset-4"
            >
              {d.title}
            </Link>
          </h3>
          <p className="mt-1.5 text-[14px] text-muted">{meta}</p>
          {d.status === "processing" && <Progress value={d.progress} />}
          {d.status === "error" && <p className="mt-3 text-[14px] text-muted">L'analyse a échoué.</p>}
        </div>
        <div className="flex shrink-0 flex-col gap-3 sm:w-[300px]">
          {d.counts.verdicts > 0 && <Distribution byLabel={d.counts.by_label} total={d.counts.verdicts} claims={d.counts.claims} />}
          {d.status === "ready" && (
            <div className="relative z-10 flex items-center gap-4 text-[13.5px] font-medium">
              <Link to={`/d/${encodeURIComponent(d.id)}`} className="inline-flex items-center gap-1 text-ink hover:underline">
                Revoir
                <IconArrowRight className="size-3.5" />
              </Link>
              <Link
                to={`/live/${encodeURIComponent(d.id)}`}
                className="inline-flex items-center gap-1 text-muted hover:text-ink hover:underline"
                aria-label={`Suivre ${d.title} en direct (simulation)`}
              >
                <IconLive className="size-4" />
                Direct simulé
              </Link>
            </div>
          )}
        </div>
      </div>
    </li>
  );
}

function Progress({ value }: { value: number }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="mt-3 max-w-md">
      <div className="flex items-center justify-between text-[13px] text-muted">
        <span className="inline-flex items-center gap-1.5">
          <IconSpinner className="size-3.5" />
          Analyse en cours
        </span>
        <span className="tabular-nums">{pct} %</span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Progression de l'analyse"
      >
        <div className="h-full rounded-full bg-ink transition-[width] duration-700" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Distribution({ byLabel, total, claims }: { byLabel: Partial<Record<VerdictLabel, number>>; total: number; claims: number }) {
  const present = VERDICT_ORDER.filter((l) => (byLabel[l] ?? 0) > 0);
  return (
    <div>
      <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-3" aria-hidden>
        {present.map((l) => (
          <span
            key={l}
            data-verdict={l}
            className="h-full bg-v-solid [&+span]:border-l-2 [&+span]:border-bg"
            style={{ width: `${((byLabel[l] ?? 0) / total) * 100}%` }}
          />
        ))}
      </div>
      <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12.5px] text-muted">
        <span className="font-semibold text-ink">{plural(total, "verdict")}</span>
        {present.map((l) => (
          <span key={l} data-verdict={l} className="inline-flex items-center gap-1">
            <span className="size-1.5 rounded-full bg-v-solid" aria-hidden />
            {byLabel[l]} {verdictLabel(l).toLowerCase()}
          </span>
        ))}
        {claims > total && <span className="text-subtle">· {claims - total} sans verdict</span>}
      </p>
    </div>
  );
}
