import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi, liveUrl, mediaUrl, parseYouTubeId } from "../api";

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
    new Response(typeof body === "string" ? body : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

describe("api client", () => {
  it("lists debates", async () => {
    const f = fakeFetch(200, [{ id: "a" }]);
    const api = createApi("", f);
    await expect(api.listDebates()).resolves.toEqual([{ id: "a" }]);
    expect(f.mock.calls[0]![0]).toBe("/api/debates");
  });

  it("gets a run with or without events", async () => {
    const f = fakeFetch(200, { debate: {} });
    const api = createApi("http://x", f);
    await api.getRun("2022 macron/lepen");
    await api.getRun("fixture", { events: false });
    expect(f.mock.calls[0]![0]).toBe("http://x/api/debates/2022%20macron%2Flepen");
    expect(f.mock.calls[1]![0]).toBe("http://x/api/debates/fixture?events=0");
  });

  it("posts analyze and review bodies as JSON", async () => {
    const f = fakeFetch(200, { debate_id: "yt-abc" });
    const api = createApi("", f);
    await expect(api.analyze("https://youtu.be/dQw4w9WgXcQ")).resolves.toEqual({ debate_id: "yt-abc" });
    let [url, init] = f.mock.calls[0]!;
    expect(url).toBe("/api/analyze");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ url: "https://youtu.be/dQw4w9WgXcQ" });
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");

    await api.reviewVerdict("fixture", "c0003", { label: "trompeur", reviewed: true });
    [url, init] = f.mock.calls[1]!;
    expect(url).toBe("/api/debates/fixture/verdicts/c0003");
    expect(JSON.parse(String(init?.body))).toEqual({ label: "trompeur", reviewed: true });
  });

  it("maps HTTP errors to ApiError with the server detail", async () => {
    const api = createApi("", fakeFetch(404, { detail: "unknown debate" }));
    const err = await api.getRun("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("unknown debate");
    const err2 = await createApi("", fakeFetch(500, "oops")).listDebates().catch((e: unknown) => e);
    expect((err2 as ApiError).message).toBe("Erreur 500");
  });

  it("maps network failures to status 0", async () => {
    const api = createApi("", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    await expect(api.listDebates()).rejects.toMatchObject({ status: 0 });
  });
});

describe("url helpers", () => {
  it("builds live and media URLs", () => {
    expect(liveUrl("fixture", 12.345, 10)).toBe("/api/debates/fixture/live?from=12.3&speed=10");
    expect(liveUrl("fixture", -5, 1)).toBe("/api/debates/fixture/live?from=0&speed=1");
    expect(mediaUrl("a b")).toBe("/media/a%20b");
  });

  it("parses YouTube links", () => {
    const id = "dQw4w9WgXcQ";
    for (const u of [
      `https://www.youtube.com/watch?v=${id}`,
      `https://youtube.com/watch?v=${id}&t=42s`,
      `https://m.youtube.com/watch?v=${id}`,
      `https://youtu.be/${id}?si=abc`,
      `youtu.be/${id}`,
      `https://www.youtube.com/live/${id}`,
      `https://www.youtube.com/shorts/${id}`,
      `https://www.youtube.com/embed/${id}`,
      id,
    ])
      expect(parseYouTubeId(u), u).toBe(id);
    for (const u of ["", "https://vimeo.com/123", "https://www.youtube.com/watch?v=short", "pas une url"])
      expect(parseYouTubeId(u), u).toBeNull();
  });
});

describe("operator token", () => {
  it("sends X-Sic-Operator on write requests once a token is set", async () => {
    localStorage.setItem("sic.operatorToken", "secret-1");
    const calls: [string, RequestInit | undefined][] = [];
    const fake = (async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify({ debate_id: "yt-x" }), { status: 200 });
    }) as typeof fetch;
    const a = createApi("", fake);
    await a.analyze("https://youtu.be/elgTG9oDiJY");
    expect((calls[0]![1]!.headers as Record<string, string>)["X-Sic-Operator"]).toBe("secret-1");
    localStorage.removeItem("sic.operatorToken");
  });
});
