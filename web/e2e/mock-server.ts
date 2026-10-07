/**
 * Tiny mock of the Sic API (docs/API.md) for e2e tests and offline UI work.
 * Serves data/fixtures/sample-run.json. Run: `bun e2e/mock-server.ts` (PORT, default 8797).
 *
 * Test-only helpers (not part of the contract):
 *   POST /__mock/reset          reset in-memory state
 *   POST /__mock/drop-next      the next live stream drops its connection after 3 events
 *   GET  /__mock/reviews        operator reviews received so far
 */
import { resolve } from "node:path";
import type { Claim, DebateSummary, Run, SicEvent, Verdict, VerdictLabel } from "../src/types";

const PORT = Number(process.env.PORT ?? 8797);
const ROOT = resolve(import.meta.dir, "../..");
const FIXTURE = resolve(ROOT, "data/fixtures/sample-run.json");
const VIDEO = resolve(import.meta.dir, "assets/test.mp4");

const fixture = (await Bun.file(FIXTURE).json()) as Run;

interface State {
  runs: Map<string, Run>;
  processing: Map<string, { progress: number }>;
  reviews: { debateId: string; claimId: string; body: unknown }[];
  dropNext: boolean;
}

function freshRun(): Run {
  const run = structuredClone(fixture);
  run.debate.media = { ...run.debate.media, file: "web/e2e/assets/test.mp4", duration: 120 };
  return run;
}

function processingRun(id: string, title: string, youtubeId: string | null): Run {
  return {
    debate: {
      id,
      title,
      date: "2026-10-06",
      broadcaster: "YouTube",
      roster: [],
      media: { youtube_id: youtubeId, file: null, duration: null },
      language: "fr",
    },
    segments: [],
    claims: [],
    verdicts: [],
    events: [],
    stats: {},
  };
}

let state: State;
function reset() {
  state = { runs: new Map(), processing: new Map(), reviews: [], dropNext: false };
  const run = freshRun();
  state.runs.set(run.debate.id, run);
  const p = processingRun("demo-en-cours", "Débat régional · Grand Est (démo)", null);
  p.debate.broadcaster = "France 3";
  p.debate.date = "2026-10-05";
  state.runs.set(p.debate.id, p);
  state.processing.set(p.debate.id, { progress: 0.42 });
}
reset();

function summary(run: Run): DebateSummary {
  const by_label: Partial<Record<VerdictLabel, number>> = {};
  for (const v of run.verdicts) by_label[v.label] = (by_label[v.label] ?? 0) + 1;
  const proc = state.processing.get(run.debate.id);
  return {
    id: run.debate.id,
    title: run.debate.title,
    date: run.debate.date,
    broadcaster: run.debate.broadcaster,
    duration: run.debate.media.duration,
    status: proc ? "processing" : "ready",
    progress: proc ? proc.progress : 1,
    counts: { claims: run.claims.length, verdicts: run.verdicts.length, by_label },
  };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8" } });
const notFound = () => json({ detail: "Not found" }, 404);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function liveStream(run: Run, from: number, speed: number, drop: boolean, signal: AbortSignal): Response {
  const events = [...(run.events ?? [])].filter((e) => e.at >= from).sort((a, b) => a.at - b.at || a.seq - b.seq);
  const lastAt = events.length ? events[events.length - 1]!.at : from;
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(ctrl) {
      const send = (type: string, data: unknown, id?: number) => {
        if (signal.aborted) return;
        ctrl.enqueue(enc.encode(`${id !== undefined ? `id: ${id}\n` : ""}event: ${type}\ndata: ${JSON.stringify(data)}\n\n`));
      };
      ctrl.enqueue(enc.encode("retry: 1000\n\n"));
      const t0 = performance.now();
      const clock = () => from + ((performance.now() - t0) / 1000) * speed;
      const status = (s: Record<string, unknown>) => {
        const t = clock();
        const ev: SicEvent = { seq: 0, type: "status", t, at: t, data: s };
        send("status", ev);
      };
      status({ state: "clock", t: from });
      let i = 0;
      let sent = 0;
      let nextTick = 1000;
      while (!signal.aborted) {
        const now = performance.now() - t0;
        while (i < events.length && events[i]!.at <= clock()) {
          const ev = events[i++]!;
          send(ev.type, ev, ev.seq);
          sent++;
          if (drop && sent >= 3) {
            ctrl.close(); // simulate a network drop
            return;
          }
        }
        if (now >= nextTick) {
          status({ state: "clock", t: clock() });
          nextTick += 1000;
        }
        if (i >= events.length && clock() >= lastAt + 0.5) {
          status({ state: "end" });
          ctrl.close();
          return;
        }
        await sleep(20);
      }
      try {
        ctrl.close();
      } catch {
        /* already closed */
      }
    },
  });
  return new Response(stream, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" },
  });
}

async function serveVideo(req: Request): Promise<Response> {
  const file = Bun.file(VIDEO);
  const size = file.size;
  const range = req.headers.get("range");
  if (!range) {
    return new Response(file, { headers: { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Length": String(size) } });
  }
  const m = /bytes=(\d*)-(\d*)/.exec(range);
  let start = m?.[1] ? Number(m[1]) : 0;
  let end = m?.[2] ? Number(m[2]) : size - 1;
  if (!m?.[1] && m?.[2]) {
    start = size - Number(m[2]);
    end = size - 1;
  }
  end = Math.min(end, size - 1);
  if (start > end || start >= size) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
  }
  return new Response(file.slice(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": "video/mp4",
      "Accept-Ranges": "bytes",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Content-Length": String(end - start + 1),
    },
  });
}

function parseYouTubeId(url: string): string | null {
  const m = /(?:v=|youtu\.be\/|\/shorts\/|\/live\/|\/embed\/)([\w-]{11})/.exec(url);
  return m?.[1] ?? null;
}

const server = Bun.serve({
  port: PORT,
  idleTimeout: 0, // SSE
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname;
    const method = req.method;

    if (path === "/__mock/reset" && method === "POST") {
      reset();
      return json({ ok: true });
    }
    if (path === "/__mock/drop-next" && method === "POST") {
      state.dropNext = true;
      return json({ ok: true });
    }
    if (path === "/__mock/reviews") return json(state.reviews);

    if (path === "/api/debates" && method === "GET") {
      for (const p of state.processing.values()) p.progress = Math.min(0.97, p.progress + 0.01);
      return json([...state.runs.values()].map(summary));
    }

    if (path === "/api/analyze" && method === "POST") {
      const body = (await req.json().catch(() => ({}))) as { url?: string };
      const yt = body.url ? parseYouTubeId(body.url) : null;
      if (!yt) return json({ detail: "URL YouTube invalide" }, 422);
      const id = `yt-${yt}`;
      if (!state.runs.has(id)) {
        state.runs.set(id, processingRun(id, `Vidéo YouTube ${yt}`, yt));
        state.processing.set(id, { progress: 0.05 });
      }
      return json({ debate_id: id });
    }

    let m = /^\/api\/debates\/([^/]+)$/.exec(path);
    if (m && method === "GET") {
      const run = state.runs.get(decodeURIComponent(m[1]!));
      if (!run) return notFound();
      if (url.searchParams.get("events") === "0") {
        const { events: _omit, ...rest } = run;
        return json(rest);
      }
      return json(run);
    }

    m = /^\/api\/debates\/([^/]+)\/live$/.exec(path);
    if (m && method === "GET") {
      const run = state.runs.get(decodeURIComponent(m[1]!));
      if (!run) return notFound();
      const from = Math.max(0, Number(url.searchParams.get("from") ?? 0) || 0);
      const speed = Math.max(0.1, Number(url.searchParams.get("speed") ?? 1) || 1);
      const drop = state.dropNext;
      state.dropNext = false;
      return liveStream(run, from, speed, drop, req.signal);
    }

    m = /^\/api\/debates\/([^/]+)\/verdicts\/([^/]+)$/.exec(path);
    if (m && method === "POST") {
      const debateId = decodeURIComponent(m[1]!);
      const claimId = decodeURIComponent(m[2]!);
      const run = state.runs.get(debateId);
      const claim: Claim | undefined = run?.claims.find((c) => c.id === claimId);
      if (!run || !claim) return notFound();
      const body = (await req.json().catch(() => ({}))) as Partial<Verdict> & { reviewed?: boolean };
      state.reviews.push({ debateId, claimId, body });
      const prev = run.verdicts.find((v) => v.claim_id === claimId);
      const next: Verdict = {
        claim_id: claimId,
        label: body.label ?? prev?.label ?? "inverifiable",
        headline: body.headline ?? prev?.headline ?? "",
        explanation: body.explanation ?? prev?.explanation ?? "",
        sources: prev?.sources ?? [],
        method: prev?.method ?? "none",
        reviewed: true,
        confidence: prev?.confidence ?? 1,
        library_id: prev?.library_id ?? null,
        latency_s: prev?.latency_s ?? null,
      };
      run.verdicts = [...run.verdicts.filter((v) => v.claim_id !== claimId), next];
      return json(next);
    }

    m = /^\/media\/([^/]+)$/.exec(path);
    if (m && (method === "GET" || method === "HEAD")) {
      const run = state.runs.get(decodeURIComponent(m[1]!));
      if (!run?.debate.media.file) return notFound();
      return serveVideo(req);
    }

    return notFound();
  },
});

console.log(`[mock] Sic API mock on http://localhost:${server.port}`);
