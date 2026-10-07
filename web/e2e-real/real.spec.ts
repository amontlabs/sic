import { expect, test } from "@playwright/test";

const ID = "2022-macron-lepen";
const shot = (name: string) => `e2e/screenshots/real-${name}.png`;

test("home lists the processed 2022 debate", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Emmanuel Macron – Marine Le Pen").first()).toBeVisible();
  await page.screenshot({ path: shot("home"), fullPage: true });
});

test("replay: real transcript, claims and seeking", async ({ page }) => {
  await page.goto(`/d/${ID}?vue=tout`);
  await expect(page.getByText("Marine Le Pen").first()).toBeVisible();
  await expect(page.getByText(/Bonsoir et bienvenue/).first()).toBeVisible();
  const video = page.locator("video");
  await expect(video).toHaveCount(1);
  // open a claim later in the debate and check the player seeks there
  const card = page.locator("article, li").filter({ hasText: "1,2 million" }).first();
  await card.scrollIntoViewIfNeeded();
  await card.click();
  await expect.poll(async () => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 20_000 }).toBeGreaterThan(600);
  await page.screenshot({ path: shot("replay-detail") });
});

test("live: events stream from the real server", async ({ page }) => {
  await page.goto(`/live/${ID}?from=700&speed=10&vue=tout`);
  await expect(page.getByText(/Débat · En direct/)).toBeVisible();
  await expect(page.getByRole("list").getByRole("listitem").first()).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(8000);
  await page.screenshot({ path: shot("live") });
});
