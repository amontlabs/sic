import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ClaimCard } from "../ClaimCard";
import { EqualTreatment } from "../EqualTreatment";
import { FilterBar } from "../FilterBar";
import { Transcript } from "../Transcript";
import { DEFAULT_FILTER, filterClaims, type ClaimFilter } from "../../lib/filters";
import { buildSpeakers } from "../../lib/speakers";
import { loadFixture } from "../../test/fixture";
import type { Verdict } from "../../types";

const run = loadFixture();
const speakers = buildSpeakers(run.debate.roster);
const verdicts = new Map<string, Verdict>(run.verdicts.map((v) => [v.claim_id, v]));
const claim = (id: string) => run.claims.find((c) => c.id === id)!;

describe("ClaimCard", () => {
  it("shows speaker, time, quote, verdict label, headline, review badge and method", async () => {
    const onSelect = vi.fn();
    render(<ClaimCard claim={claim("c0003")} verdict={verdicts.get("c0003")} speaker={speakers.get("candidate-b")!} onSelect={onSelect} />);
    const card = screen.getByTestId("claim-card");
    expect(card).toHaveTextContent("Candidate B");
    expect(card).toHaveTextContent("0:24");
    expect(card).toHaveTextContent("j’ai toujours voté contre la réforme des retraites.");
    expect(within(card).getByTestId("verdict-pill")).toHaveTextContent("Faux");
    expect(card).toHaveTextContent("A voté pour en première lecture");
    expect(within(card).getByTestId("review-badge")).toHaveTextContent("Relu");
    expect(card).toHaveTextContent("Méthode : Données officielles");
    expect(card).toHaveTextContent("Assemblée nationale");
    await userEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith(claim("c0003"));
  });

  it("shows Auto for automatic verdicts", () => {
    render(<ClaimCard claim={claim("c0007")} verdict={verdicts.get("c0007")} speaker={speakers.get("candidate-b")!} />);
    expect(screen.getByTestId("review-badge")).toHaveTextContent("Auto");
  });

  it("shows a placeholder while verifying and a neutral tag for promises", () => {
    const { rerender } = render(<ClaimCard claim={claim("c0001")} verdict={undefined} speaker={speakers.get("candidat-a")!} />);
    expect(screen.getByText("Vérification en cours…")).toBeInTheDocument();
    expect(screen.queryByTestId("verdict-pill")).toBeNull();
    rerender(<ClaimCard claim={claim("c0005")} verdict={undefined} speaker={speakers.get("candidate-b")!} />);
    expect(screen.getByText("Promesse · non vérifiable")).toBeInTheDocument();
  });

  it("never renders the word « mensonge »", () => {
    const { container } = render(
      <>
        {run.claims.map((c) => (
          <ClaimCard key={c.id} claim={c} verdict={verdicts.get(c.id)} speaker={speakers.get(c.speaker)!} />
        ))}
      </>,
    );
    expect(container.textContent?.toLowerCase()).not.toContain("mensonge");
  });
});

function FilterHarness() {
  const [filter, setFilter] = useState<ClaimFilter>(DEFAULT_FILTER);
  const items = run.claims.map((c) => ({ claim: c, verdict: verdicts.get(c.id) }));
  const visible = filterClaims(items, filter);
  return (
    <>
      <FilterBar
        filter={filter}
        onChange={setFilter}
        speakers={[speakers.get("candidat-a")!, speakers.get("candidate-b")!]}
        counts={{ notables: 3, all: 7, byLabel: { exact: 2, faux: 2, imprecis: 1, plutot_exact: 1 } }}
      />
      <output data-testid="ids">{visible.map((i) => i.claim.id).join(",")}</output>
    </>
  );
}

describe("FilterBar", () => {
  it("switches between Notables, Tout, speaker and verdict filters", async () => {
    const u = userEvent.setup();
    render(<FilterHarness />);
    const ids = () => screen.getByTestId("ids").textContent;
    expect(screen.getByRole("button", { name: /Notables/ })).toHaveAttribute("aria-pressed", "true");
    expect(ids()).toBe("c0003,c0004,c0007");
    await u.click(screen.getByRole("button", { name: /^Tout/ }));
    expect(ids()).toBe("c0001,c0002,c0003,c0004,c0005,c0006,c0007");
    await u.click(screen.getByRole("button", { name: "Seulement Candidat A" }));
    expect(ids()).toBe("c0001,c0002,c0006");
    await u.click(screen.getByRole("button", { name: "Seulement Candidat A" }));
    await u.click(screen.getByRole("button", { name: /^Exact/ }));
    expect(ids()).toBe("c0002,c0006");
    expect(screen.getByRole("button", { name: /^Trompeur/ })).toBeDisabled();
    await u.click(screen.getByRole("button", { name: /^Exact/ }));
    expect(ids()).toBe("c0001,c0002,c0003,c0004,c0005,c0006,c0007");
  });
});

describe("Transcript", () => {
  it("groups turns, highlights the active segment and quotes, and seeks on click", async () => {
    const onSeek = vi.fn();
    const onSelectClaim = vi.fn();
    // jsdom has no layout: stub scrollTo
    Element.prototype.scrollTo = vi.fn();
    render(
      <Transcript
        segments={run.segments}
        claims={run.claims}
        verdicts={verdicts}
        speakers={speakers}
        activeIndex={3}
        follow
        onFollowChange={() => undefined}
        onSeek={onSeek}
        onSelectClaim={onSelectClaim}
      />,
    );
    expect(screen.getAllByTestId("turn")).toHaveLength(8);
    const active = document.querySelector('[data-active="true"]')!;
    expect(active).toHaveTextContent("C'est faux, et j'ai toujours voté contre la réforme des retraites.");
    const marks = screen.getAllByTestId("claim-mark");
    expect(marks).toHaveLength(7);
    expect(marks.find((m) => m.textContent?.startsWith("j'ai"))).toHaveAttribute("data-verdict", "faux");
    await userEvent.click(screen.getByText(/Nous avons créé/));
    expect(onSeek).toHaveBeenCalledWith(14);
    await userEvent.click(screen.getAllByRole("button", { name: "Voir la vérification : Faux" })[0]!);
    expect(onSelectClaim).toHaveBeenCalledWith(claim("c0003"));
  });
});

describe("EqualTreatment", () => {
  it("shows the same breakdown for every candidate", () => {
    render(
      <MemoryRouter>
        <EqualTreatment
          speakers={[speakers.get("candidat-a")!, speakers.get("candidate-b")!]}
          claims={run.claims}
          verdicts={verdicts}
        />
      </MemoryRouter>,
    );
    const box = screen.getByTestId("equal-treatment");
    expect(within(box).getAllByText("3 vérifications")).toHaveLength(2);
    expect(within(box).getByRole("img", { name: "Candidat A : 2 exact, 1 plutôt exact" })).toBeInTheDocument();
    expect(within(box).getByRole("img", { name: "Candidate B : 1 imprécis, 2 faux" })).toBeInTheDocument();
    expect(box).toHaveTextContent("Même méthode pour tous. Chaque verdict cite au moins une source.");
  });
});
