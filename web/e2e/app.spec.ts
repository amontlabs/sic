import { expect, test } from "@playwright/test";
import { blockYouTube, MOCK, resetMock, streamClock, videoTime } from "./helpers";

test.beforeEach(async ({ page, request }) => {
  await resetMock(request);
  await blockYouTube(page);
});

test.describe("home", () => {
  test("lists debates with counts and processing progress", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Ce qu’ils disent, vérifié en direct." })).toBeVisible();
    const rows = page.getByTestId("debate-row");
    await expect(rows).toHaveCount(2);
    const fixture = rows.filter({ hasText: "Débat fictif (données de test)" });
    await expect(fixture).toContainText("20 avril 2022 · Test TV");
    await expect(fixture).toContainText("6 verdicts");
    await expect(fixture).toContainText("2 faux");
    const processing = rows.filter({ hasText: "Grand Est" });
    await expect(processing.getByRole("progressbar")).toBeVisible();
    await expect(processing).toContainText("Analyse en cours");
    await fixture.getByRole("link", { name: "Débat fictif (données de test)", exact: true }).click();
    await expect(page).toHaveURL(/\/d\/fixture$/);
  });

  test("analyze a YouTube link navigates to the replay page", async ({ page, request }) => {
    await page.goto("/");
    const input = page.getByLabel("Analyser une vidéo");
    await input.fill("https://vimeo.com/123");
    await page.getByRole("button", { name: "Analyser une vidéo" }).click();
    await expect(page.getByRole("alert")).toContainText("lien YouTube valide");
    await input.fill("https://www.youtube.com/watch?v=dQw4w9WgXcQ");
    await page.getByRole("button", { name: "Analyser une vidéo" }).click();
    await expect(page).toHaveURL(/\/d\/yt-dQw4w9WgXcQ$/);
    await expect(page.getByRole("status").filter({ hasText: "Analyse en cours" })).toBeVisible();
    const list = await (await request.get(`${MOCK}/api/debates`)).json();
    expect(list.map((d: { id: string }) => d.id)).toContain("yt-dQw4w9WgXcQ");
  });
});

test.describe("replay", () => {
  test("clicking a claim seeks the video and opens the detail with sources", async ({ page }) => {
    await page.goto("/d/fixture");
    await expect(page.locator("video")).toHaveAttribute("src", "/media/fixture");
    await expect.poll(() => page.locator("video").evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThan(0);
    await page.locator('[data-testid="claim-card"][data-claim-id="c0007"]').click();
    await expect.poll(() => videoTime(page)).toBeCloseTo(70, 0);
    const detail = page.getByTestId("claim-detail");
    await expect(detail).toBeVisible();
    await expect(detail).toContainText("L’Allemagne a fermé toutes ses centrales nucléaires"); // typographic apostrophe in quotes
    await expect(detail).toContainText("L'Allemagne a fermé toutes ses centrales nucléaires en 2021."); // standalone claim
    await expect(detail.getByTestId("verdict-pill")).toHaveText("Faux");
    await expect(detail.getByTestId("review-badge")).toHaveText("Auto");
    const sources = detail.getByTestId("sources");
    await expect(sources).toContainText("Gouvernement fédéral");
    // bare homepage URL → labelled as the publisher's site, not as a specific source page
    await expect(sources.getByRole("link", { name: /Site de l’éditeur \(bundesregierung\.de\)/ })).toHaveAttribute(
      "href",
      "https://www.bundesregierung.de/",
    );
    await expect(page).toHaveURL(/c=c0007/);
    await expect(detail.getByRole("link", { name: "Signaler une erreur" })).toHaveAttribute("href", /^mailto:corrections@sic\.vote\?/);

    // "Voir l'extrait" seeks (slightly before) and plays
    await page.locator("video").evaluate((v: HTMLVideoElement) => {
      v.muted = true;
      v.currentTime = 5;
    });
    await detail.getByRole("button", { name: "Voir l'extrait" }).click();
    await expect.poll(() => videoTime(page)).toBeGreaterThan(69);
    await expect.poll(() => page.locator("video").evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);

    await detail.getByRole("button", { name: /Fermer et revenir/ }).click();
    await expect(page.getByTestId("claim-detail")).toBeHidden();
    await expect(page).not.toHaveURL(/c=c0007/);
  });

  test("filters: Notables by default, Tout, per speaker, per verdict", async ({ page }) => {
    await page.goto("/d/fixture");
    const cards = page.getByTestId("claim-card");
    await expect(cards).toHaveCount(3);
    await expect(page.getByRole("button", { name: /^Notables/ })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: /^Tout/ }).click();
    await expect(cards).toHaveCount(7);
    await expect(page).toHaveURL(/vue=tout/);
    await expect(page.getByText("Promesse · non vérifiable")).toBeVisible();
    await page.getByRole("button", { name: "Seulement Candidat A" }).click();
    await expect(cards).toHaveCount(3);
    for (const c of await cards.all()) await expect(c).toContainText("Candidat A");
    await page.getByRole("button", { name: "Seulement Candidat A" }).click();
    await page.getByRole("button", { name: /^Faux/ }).click();
    await expect(cards).toHaveCount(2);
    for (const c of await cards.all()) await expect(c.getByTestId("verdict-pill")).toHaveText("Faux");
    // reload keeps the filter (URL state)
    await page.reload();
    await expect(cards).toHaveCount(2);
  });

  test("transcript follows playback, can be scrolled freely, and seeks on click", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 640 }); // small panel so scrolling is needed
    await page.goto("/d/fixture");
    const transcript = page.getByTestId("transcript");
    await expect(transcript.getByTestId("turn").first()).toBeVisible();
    await page.locator("video").evaluate(async (v: HTMLVideoElement) => {
      v.muted = true;
      v.currentTime = 71;
      await v.play();
    });
    const active = transcript.locator("[data-active]");
    await expect(active).toHaveAttribute("data-segment-id", "s0009");
    await expect(active).toBeInViewport();
    // the claim quote in the active segment is underlined with its verdict
    await expect(active.getByTestId("claim-mark")).toHaveAttribute("data-verdict", "faux");

    // user scrolls → follow mode turns off
    await transcript.hover();
    await page.mouse.wheel(0, -2000);
    await expect(page.getByTestId("follow-toggle")).not.toBeChecked();
    await expect(page.getByRole("button", { name: "Revenir à la lecture" })).toBeVisible();

    // clicking a sentence seeks
    await transcript.getByText("Nous avons créé plus d'un million d'emplois.").click();
    await expect.poll(() => videoTime(page)).toBeGreaterThanOrEqual(14);
    await expect.poll(() => videoTime(page)).toBeLessThan(17);
    await expect(transcript.locator("[data-active]")).toHaveAttribute("data-segment-id", "s0002");
    await expect(page.getByTestId("follow-toggle")).toBeChecked();
  });

  test("operator review posts a new label", async ({ page, request }) => {
    await page.goto("/d/fixture?operator=1&c=c0004");
    const panel = page.getByTestId("operator-review");
    await expect(panel).toBeVisible();
    await panel.getByRole("button", { name: "Trompeur" }).click();
    await panel.getByLabel("Titre").fill("Hausse de 560 Md€, pas 600");
    await panel.getByRole("button", { name: "Enregistrer la correction" }).click();
    await expect(panel.getByRole("status")).toHaveText("Correction enregistrée.");
    const detail = page.getByTestId("claim-detail");
    await expect(detail.getByTestId("verdict-pill")).toHaveText("Trompeur");
    await expect(detail.getByTestId("review-badge")).toHaveText("Relu");
    await expect(detail).toContainText("Hausse de 560 Md€, pas 600");

    // validate another one as-is
    await detail.getByRole("button", { name: /Fermer et revenir/ }).click();
    await page.locator('[data-claim-id="c0007"]').click();
    await page.getByTestId("operator-review").getByRole("button", { name: "Valider" }).click();
    await expect(page.getByTestId("operator-review").getByRole("status")).toHaveText("Verdict validé.");
    await expect(page.getByTestId("claim-detail").getByTestId("review-badge")).toHaveText("Relu");

    const reviews = await (await request.get(`${MOCK}/__mock/reviews`)).json();
    expect(reviews).toEqual([
      {
        debateId: "fixture",
        claimId: "c0004",
        body: {
          label: "trompeur",
          headline: "Hausse de 560 Md€, pas 600",
          explanation: "Exemple fictif : la dette au sens de Maastricht a augmenté d'environ 560 milliards entre 2017 et 2021.",
          reviewed: true,
        },
      },
      { debateId: "fixture", claimId: "c0007", body: { label: "faux", reviewed: true } },
    ]);
  });

  test("operator controls are hidden without ?operator=1", async ({ page }) => {
    await page.goto("/d/fixture?c=c0004");
    await expect(page.getByTestId("claim-detail")).toBeVisible();
    await expect(page.getByTestId("operator-review")).toHaveCount(0);
  });

  test("share card produces a 1080×1350 PNG", async ({ page }) => {
    await page.goto("/d/fixture?c=c0003");
    await page.getByTestId("claim-detail").getByRole("button", { name: "Partager" }).click();
    const img = page.getByTestId("share-preview");
    await expect(img).toBeVisible();
    const size = await img.evaluate(async (el: HTMLImageElement) => {
      await el.decode();
      return [el.naturalWidth, el.naturalHeight];
    });
    expect(size).toEqual([1080, 1350]);
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("share-download").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("sic-candidate-b-24s.png");
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const c of stream) chunks.push(c as Buffer);
    const png = Buffer.concat(chunks);
    expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(png.length).toBeGreaterThan(20_000);
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("share-dialog")).toBeHidden();
  });
});

test.describe("live", () => {
  test("receives events at speed 10 and shows verdict cards", async ({ page }) => {
    await page.goto("/live/fixture?speed=10");
    await expect(page.getByTestId("live-status")).toHaveAttribute("data-state", /open|ended/);
    const feed = page.getByTestId("live-feed");
    // Notables by default
    await expect(page.getByRole("button", { name: /^Notables/ })).toHaveAttribute("aria-pressed", "true");
    await expect(feed.getByTestId("claim-card").first()).toBeVisible({ timeout: 8000 });
    await expect(page.getByTestId("live-ended")).toBeVisible({ timeout: 15_000 });
    await expect(feed.getByTestId("claim-card")).toHaveCount(3);
    await expect(feed.getByTestId("verdict-pill")).toHaveText(["Faux", "Imprécis", "Faux"]); // newest first
    await page.getByRole("button", { name: /^Tout/ }).click();
    await expect(feed.getByTestId("claim-card")).toHaveCount(7);
    await expect(page.getByTestId("live-ticker")).toContainText("L'Allemagne a fermé toutes ses centrales nucléaires");
    await expect(page.getByTestId("live-topic")).toContainText("Énergie");
    // sources are one tap away
    await feed.locator('[data-claim-id="c0003"]').click();
    await expect(page.getByTestId("claim-detail").getByTestId("sources")).toContainText("Assemblée nationale");
    await page.getByRole("button", { name: "Fermer", exact: true }).click();
    await expect(page.getByTestId("claim-detail")).toHaveCount(0);
  });

  test("shows the placeholder until the verdict lands", async ({ page }) => {
    // c0004: claim available at 39, verdict at 46.8
    await page.goto("/live/fixture?speed=4&from=36");
    const card = page.locator('[data-testid="claim-card"][data-claim-id="c0004"]');
    await expect(card).toContainText("Vérification en cours…");
    await expect(card.getByTestId("verdict-pill")).toHaveText("Imprécis", { timeout: 8000 });
  });

  test("TV delay defers display", async ({ page }) => {
    test.setTimeout(45_000);
    await page.goto("/live/fixture?speed=4&delay=20");
    await expect(page.getByTestId("delay-value")).toHaveText("+20 s");
    await page.getByRole("button", { name: /^Tout/ }).click();
    const cards = page.getByTestId("live-feed").getByTestId("claim-card");
    // stream clock past 30: without delay c0001 (at 16) and c0002 (at 22) would be visible
    await expect.poll(() => streamClock(page), { timeout: 15_000, intervals: [100] }).toBeGreaterThan(30);
    expect(await streamClock(page)).toBeLessThan(35);
    await expect(cards).toHaveCount(0);
    await expect(page.getByText(/en attente pour rester synchronisé/)).toBeVisible();
    // c0001 shows once clock >= 16 + 20
    await expect.poll(() => streamClock(page), { timeout: 15_000 }).toBeGreaterThan(37);
    await expect(cards.first()).toHaveAttribute("data-claim-id", /c000[12]/);
    // moving the slider back releases held cards immediately
    await page.getByTestId("delay-slider").fill("-10");
    await expect(page.getByTestId("delay-value")).toHaveText("−10 s");
    await expect.poll(async () => cards.count()).toBeGreaterThanOrEqual(3);
  });

  test("reconnects after a dropped connection without duplicates", async ({ page, request }) => {
    await request.post(`${MOCK}/__mock/drop-next`);
    await page.goto("/live/fixture?speed=10");
    await page.getByRole("button", { name: /^Tout/ }).click();
    await expect(page.getByTestId("live-ended")).toBeVisible({ timeout: 20_000 });
    const cards = page.getByTestId("live-feed").getByTestId("claim-card");
    await expect(cards).toHaveCount(7);
    const ids = await cards.evaluateAll((els) => els.map((e) => e.getAttribute("data-claim-id")));
    expect(new Set(ids).size).toBe(7);
  });

  test("speed selector reconnects with the new speed", async ({ page }) => {
    const urls: string[] = [];
    page.on("request", (r) => {
      if (r.url().includes("/live?")) urls.push(r.url());
    });
    await page.goto("/live/fixture?speed=1");
    await expect(page.getByTestId("live-status")).toHaveAttribute("data-state", "open");
    await page.getByRole("button", { name: "10×" }).click();
    await expect(page).toHaveURL(/speed=10/);
    await expect.poll(() => urls.some((u) => /speed=10/.test(u))).toBe(true);
    await expect(page.getByTestId("live-feed").getByTestId("claim-card").first()).toBeVisible({ timeout: 10_000 });
  });

  test("simulated TV follows the stream clock minus the delay", async ({ page }) => {
    await page.goto("/live/fixture?speed=2&from=20");
    await page.getByTestId("sim-tv").check({ force: true });
    const video = page.getByTestId("sim-tv-player").locator("video");
    await expect(video).toBeVisible();
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => !v.paused && v.playbackRate === 2), { timeout: 8000 }).toBe(true);
    const [vt, clock] = [await video.evaluate((v: HTMLVideoElement) => v.currentTime), await streamClock(page)];
    expect(Math.abs(vt - clock)).toBeLessThan(3);
  });
});

test.describe("responsive", () => {
  test.use({ viewport: { width: 360, height: 740 } });
  for (const path of ["/", "/d/fixture?vue=tout", "/d/fixture?c=c0003", "/live/fixture?speed=10"]) {
    test(`no horizontal overflow at 360px: ${path}`, async ({ page }) => {
      await page.goto(path);
      await page.waitForLoadState("networkidle").catch(() => undefined);
      await page.waitForTimeout(500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
});

test.describe("accessibility basics", () => {
  test("claim cards and transcript are keyboard operable", async ({ page }) => {
    await page.goto("/d/fixture?vue=tout");
    const card = page.locator('[data-claim-id="c0002"]');
    await card.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("claim-detail")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Vérification" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("claim-detail")).toBeHidden();
    const seg = page.locator('[data-segment-id="s0008"]');
    await seg.focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => videoTime(page)).toBeCloseTo(60, 0);
  });

  test("the word « mensonge » never appears", async ({ page }) => {
    for (const path of ["/", "/d/fixture?vue=tout", "/d/fixture?c=c0003&operator=1"]) {
      await page.goto(path);
      await page.waitForTimeout(300);
      expect((await page.locator("body").innerText()).toLowerCase()).not.toContain("mensonge");
    }
  });
});
