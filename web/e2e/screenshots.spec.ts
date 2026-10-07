import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

/** Visual review: every screen × desktop/mobile × light/dark → e2e/screenshots/. */
const OUT = resolve(import.meta.dirname, "screenshots");
mkdirSync(OUT, { recursive: true });

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  mobile: { width: 390, height: 844 },
} as const;
const SCHEMES = ["light", "dark"] as const;

test.describe.configure({ mode: "serial" });
test.setTimeout(60_000);

test.beforeEach(async ({ request }) => {
  await request.post("http://localhost:8797/__mock/reset");
});

async function shot(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);
  await page.screenshot({ path: resolve(OUT, `${name}.png`) });
}

for (const [vp, size] of Object.entries(VIEWPORTS)) {
  for (const scheme of SCHEMES) {
    const isMobile = vp === "mobile";
    test.describe(`${vp} ${scheme}`, () => {
      test.use({ viewport: size, colorScheme: scheme, deviceScaleFactor: isMobile ? 2 : 1, hasTouch: isMobile, isMobile });

      test(`home ${vp} ${scheme}`, async ({ page }) => {
        await page.goto("/");
        await expect(page.getByTestId("debate-row").first()).toBeVisible();
        await shot(page, `home-${vp}-${scheme}`);
      });

      test(`replay ${vp} ${scheme}`, async ({ page }) => {
        await page.goto("/d/fixture?vue=tout");
        await expect(page.getByTestId("claim-card").first()).toBeVisible();
        // put the playhead on a claim so the transcript highlight shows
        await page.evaluate(() => {
          const v = document.querySelector("video");
          if (v) v.currentTime = 31;
        });
        await page.waitForTimeout(400);
        await shot(page, `replay-${vp}-${scheme}`);
        if (isMobile) {
          await page.getByRole("tab", { name: "Transcription" }).click();
          await shot(page, `replay-transcript-${vp}-${scheme}`);
          await page.getByRole("tab", { name: "Bilan" }).click();
          await shot(page, `replay-bilan-${vp}-${scheme}`);
          await page.getByRole("tab", { name: /Vérifications/ }).click();
        }
        await page.locator('[data-claim-id="c0003"]').click();
        await expect(page.getByTestId("claim-detail")).toBeVisible();
        await shot(page, `replay-detail-${vp}-${scheme}`);
      });

      test(`live ${vp} ${scheme}`, async ({ page }) => {
        // mid-stream: c0004 is still being verified (claim at 39, verdict at 46.8)
        await page.goto("/live/fixture?speed=4");
        await page.getByRole("button", { name: /^Tout/ }).click();
        await expect(page.locator('[data-claim-id="c0004"]')).toContainText("Vérification en cours…", { timeout: 15_000 });
        await page.evaluate(() => window.scrollTo(0, 0));
        await shot(page, `live-${vp}-${scheme}`);
        if (!isMobile) {
          await page.goto("/live/fixture?speed=2&from=60&tv=1");
          await expect(page.locator('[data-claim-id="c0006"]')).toBeVisible({ timeout: 10_000 });
          await page.waitForTimeout(1500);
          await shot(page, `live-simtv-${vp}-${scheme}`);
        }
      });
    });
  }
}

test.describe("share card", () => {
  test.use({ viewport: VIEWPORTS.desktop });
  test("share card png", async ({ page }) => {
    await page.goto("/d/fixture?c=c0003");
    await page.getByRole("button", { name: "Partager" }).click();
    const img = page.getByTestId("share-preview");
    await expect(img).toBeVisible();
    const b64 = await img.evaluate(async (el: HTMLImageElement) => {
      const blob = await (await fetch(el.src)).blob();
      const buf = new Uint8Array(await blob.arrayBuffer());
      let s = "";
      for (const c of buf) s += String.fromCharCode(c);
      return btoa(s);
    });
    writeFileSync(resolve(OUT, "share-card.png"), Buffer.from(b64, "base64"));
    await shot(page, "share-dialog-desktop-light");
  });
});
