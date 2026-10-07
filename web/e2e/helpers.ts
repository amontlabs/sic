import type { APIRequestContext, Page } from "@playwright/test";

export const MOCK = `http://localhost:${process.env.MOCK_PORT ?? 8797}`;

export async function resetMock(request: APIRequestContext) {
  await request.post(`${MOCK}/__mock/reset`);
}

/** Never hit YouTube from tests. */
export async function blockYouTube(page: Page) {
  await page.route(/youtube(-nocookie)?\.com|ytimg\.com|googlevideo\.com/, (r) => r.abort());
}

export async function videoTime(page: Page): Promise<number> {
  return page.locator("video").first().evaluate((v: HTMLVideoElement) => v.currentTime);
}

export async function streamClock(page: Page): Promise<number> {
  return Number(await page.getByTestId("live-main").getAttribute("data-stream-clock"));
}
