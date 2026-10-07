import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router";
import type { Verdict, VerdictLabel, VerdictMethod } from "../types";
import { METHOD_LABELS, reviewLabel, verdictLabel } from "../lib/verdict";
import type { SpeakerInfo } from "../lib/speakers";
import { IconBolt, IconBook, IconChart, IconDash, IconGlobe, IconUserCheck } from "./icons";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** Wordmark: editorial "[sic]". */
export function Logo({ size = "md", asLink = true }: { size?: "sm" | "md" | "lg"; asLink?: boolean }) {
  const cls = {
    sm: "text-[22px]",
    md: "text-[26px]",
    lg: "text-[44px] sm:text-[56px]",
  }[size];
  const mark = (
    <span className={cx("font-serif font-semibold leading-none tracking-tight text-ink", cls)} aria-label="Sic">
      <span className="text-subtle font-normal" aria-hidden>
        [
      </span>
      <span aria-hidden>sic</span>
      <span className="text-subtle font-normal" aria-hidden>
        ]
      </span>
    </span>
  );
  return asLink ? (
    <Link to="/" className="inline-flex items-center rounded-sm" aria-label="Sic — accueil">
      {mark}
    </Link>
  ) : (
    mark
  );
}

export function VerdictPill({ label, size = "md" }: { label: VerdictLabel; size?: "sm" | "md" | "lg" }) {
  return (
    <span
      data-verdict={label}
      data-testid="verdict-pill"
      className={cx(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full bg-v-bg font-semibold text-v-fg ring-1 ring-inset ring-v-solid/25",
        size === "sm" && "px-2 py-0.5 text-[12px]",
        size === "md" && "px-2.5 py-1 text-[13px]",
        size === "lg" && "px-3.5 py-1.5 text-[15px]",
      )}
    >
      <span className="size-2 rounded-full bg-v-solid" aria-hidden />
      {verdictLabel(label)}
    </span>
  );
}

export function PendingPill() {
  return (
    <span
      data-verdict="pending"
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-dashed border-line-strong px-2.5 py-1 text-[13px] font-medium text-muted"
    >
      <span className="size-2 rounded-full bg-v-solid animate-live" aria-hidden />
      Vérification en cours…
    </span>
  );
}

export function NeutralTag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full border border-line px-2.5 py-1 text-[12.5px] font-medium text-muted">
      {children}
    </span>
  );
}

export function ReviewBadge({ verdict }: { verdict: Pick<Verdict, "reviewed"> }) {
  const r = reviewLabel(verdict);
  return (
    <span
      title={r.long}
      aria-label={r.long}
      data-testid="review-badge"
      className={cx(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold uppercase tracking-wide",
        verdict.reviewed ? "bg-ink text-bg" : "border border-line-strong text-muted",
      )}
    >
      {verdict.reviewed ? <IconUserCheck className="size-3.5" /> : <IconBolt className="size-3.5" />}
      {r.short}
    </span>
  );
}

const METHOD_ICON: Record<VerdictMethod, (p: { className?: string }) => ReactNode> = {
  library: IconBook,
  data: IconChart,
  web: IconGlobe,
  none: IconDash,
};

export function MethodIcon({ method, withLabel = false }: { method: VerdictMethod; withLabel?: boolean }) {
  const Icon = METHOD_ICON[method];
  const label = METHOD_LABELS[method];
  return (
    <span className="inline-flex items-center gap-1 text-[12px] text-subtle" title={`Méthode : ${label}`}>
      <Icon className="size-4" />
      {withLabel ? <span>{label}</span> : <span className="sr-only">Méthode : {label}</span>}
    </span>
  );
}

export function SpeakerName({ speaker, className }: { speaker: SpeakerInfo; className?: string }) {
  return (
    <span className={cx("inline-flex min-w-0 items-center gap-1.5 font-semibold text-ink", className)}>
      <span className="size-2.5 shrink-0 rounded-full" style={{ background: speaker.color }} aria-hidden />
      <span className="truncate">{speaker.name}</span>
    </span>
  );
}

/** Toggle chip used in filter bars. */
export function Chip({
  active,
  children,
  className,
  ...rest
}: { active: boolean; children: ReactNode } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cx(
        "inline-flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-[13px] font-medium transition-colors disabled:opacity-40",
        active ? "border-ink bg-ink text-bg" : "border-line-strong bg-surface text-ink hover:border-ink/40 hover:bg-surface-2",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: { variant?: "primary" | "secondary" | "ghost"; size?: "sm" | "md" } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50",
        size === "md" ? "h-10 px-4 text-[14px]" : "h-8 px-3 text-[13px]",
        variant === "primary" && "bg-ink text-bg hover:bg-ink/85",
        variant === "secondary" && "border border-line-strong bg-surface text-ink hover:bg-surface-2",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-ink",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

export const buttonClass = (variant: "primary" | "secondary" | "ghost" = "secondary", size: "sm" | "md" = "md") =>
  cx(
    "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors",
    size === "md" ? "h-10 px-4 text-[14px]" : "h-8 px-3 text-[13px]",
    variant === "primary" && "bg-ink text-bg hover:bg-ink/85",
    variant === "secondary" && "border border-line-strong bg-surface text-ink hover:bg-surface-2",
    variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-ink",
  );
