import { useState } from "react";
import type { Claim, Verdict, VerdictLabel } from "../types";
import { api, ApiError } from "../lib/api";
import { VERDICT_ORDER, verdictLabel } from "../lib/verdict";
import { Button, Chip } from "./ui";

interface Props {
  debateId: string;
  claim: Claim;
  verdict: Verdict | null;
  onUpdated: (v: Verdict) => void;
}

/** Visible with ?operator=1. Posts to POST /api/debates/{id}/verdicts/{claim_id}. Mount with key={claim.id}. */
export function OperatorReview({ debateId, claim, verdict, onUpdated }: Props) {
  const [label, setLabel] = useState<VerdictLabel | null>(verdict?.label ?? null);
  const [headline, setHeadline] = useState(verdict?.headline ?? "");
  const [explanation, setExplanation] = useState(verdict?.explanation ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const changed =
    !!verdict && (label !== verdict.label || headline !== verdict.headline || explanation !== verdict.explanation);

  async function submit(mode: "validate" | "save") {
    if (!label) return;
    setBusy(true);
    setMsg(null);
    try {
      const body =
        mode === "validate"
          ? { label, reviewed: true as const }
          : {
              label,
              ...(headline.trim() ? { headline: headline.trim() } : {}),
              ...(explanation.trim() ? { explanation: explanation.trim() } : {}),
              reviewed: true as const,
            };
      const updated = await api.reviewVerdict(debateId, claim.id, body);
      onUpdated(updated);
      setMsg({ ok: true, text: mode === "validate" ? "Verdict validé." : "Correction enregistrée." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? `Échec : ${e.message}` : "Échec de l'envoi." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="mt-7 rounded-xl border border-dashed border-line-strong bg-surface-2/60 p-4"
      aria-labelledby="operator-title"
      data-testid="operator-review"
    >
      <h3 id="operator-title" className="text-[13px] font-semibold uppercase tracking-[0.08em] text-muted">
        Revue opérateur
      </h3>
      <fieldset className="mt-3">
        <legend className="text-[13px] font-medium text-ink">Verdict</legend>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {VERDICT_ORDER.map((l) => (
            <Chip key={l} active={label === l} data-verdict={l} onClick={() => setLabel(l)} className="h-7 px-2.5 text-[12.5px]">
              <span className="size-2 rounded-full bg-v-solid" aria-hidden />
              {verdictLabel(l)}
            </Chip>
          ))}
        </div>
      </fieldset>
      <label className="mt-3 block text-[13px] font-medium text-ink">
        Titre
        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          maxLength={120}
          className="mt-1 block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] font-normal"
        />
      </label>
      <label className="mt-3 block text-[13px] font-medium text-ink">
        Explication
        <textarea
          value={explanation}
          onChange={(e) => setExplanation(e.target.value)}
          rows={3}
          className="mt-1 block w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[14px] font-normal"
        />
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" disabled={busy || !label || changed} onClick={() => submit("validate")}>
          Valider
        </Button>
        <Button size="sm" disabled={busy || !label || (!changed && !!verdict)} onClick={() => submit("save")}>
          Enregistrer la correction
        </Button>
      </div>
      <p role="status" className={msg?.ok === false ? "mt-2 text-[13px] text-(--v-faux-fg)" : "mt-2 text-[13px] text-muted"}>
        {msg?.text}
      </p>
    </section>
  );
}
