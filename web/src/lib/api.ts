import type { AnalyzeResponse, DebateSummary, Run, Verdict, VerdictReview } from "../types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Fetch = typeof fetch;

/** Thin typed client for docs/API.md. All paths are relative so the Vite proxy (dev) or
 * the same-origin server (prod) handles them. */
export function createApi(base = "", fetchImpl: Fetch = (...a) => fetch(...a)) {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let res: Response;
    try {
      res = await fetchImpl(base + path, {
        ...init,
        headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
      });
    } catch {
      throw new ApiError(0, "Serveur injoignable");
    }
    if (!res.ok) {
      let detail = "";
      try {
        const body: unknown = await res.json();
        if (body && typeof body === "object" && "detail" in body) detail = String((body as { detail: unknown }).detail);
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail || `Erreur ${res.status}`);
    }
    return (await res.json()) as T;
  }

  return {
    listDebates: (signal?: AbortSignal) => request<DebateSummary[]>("/api/debates", { signal }),
    getRun: (id: string, opts: { events?: boolean; signal?: AbortSignal } = {}) =>
      request<Run>(`/api/debates/${encodeURIComponent(id)}${opts.events === false ? "?events=0" : ""}`, {
        signal: opts.signal,
      }),
    analyze: (url: string) =>
      request<AnalyzeResponse>("/api/analyze", { method: "POST", body: JSON.stringify({ url }) }),
    reviewVerdict: (debateId: string, claimId: string, review: VerdictReview) =>
      request<Verdict>(
        `/api/debates/${encodeURIComponent(debateId)}/verdicts/${encodeURIComponent(claimId)}`,
        { method: "POST", body: JSON.stringify(review) },
      ),
  };
}

export type Api = ReturnType<typeof createApi>;

export const api = createApi();

export function liveUrl(id: string, from: number, speed: number): string {
  const p = new URLSearchParams({ from: String(Math.max(0, Math.round(from * 10) / 10)), speed: String(speed) });
  return `/api/debates/${encodeURIComponent(id)}/live?${p.toString()}`;
}

export function mediaUrl(id: string): string {
  return `/media/${encodeURIComponent(id)}`;
}

/** Accepts youtube.com/watch?v=, youtu.be/, /shorts/, /live/, /embed/ links. Returns the 11-char id. */
export function parseYouTubeId(input: string): string | null {
  const s = input.trim();
  if (/^[\w-]{11}$/.test(s)) return s;
  let u: URL;
  try {
    u = new URL(s.startsWith("http") ? s : `https://${s}`);
  } catch {
    return null;
  }
  const host = u.hostname.replace(/^(www\.|m\.|music\.)/, "");
  let id: string | null = null;
  if (host === "youtu.be") id = u.pathname.slice(1).split("/")[0] ?? null;
  else if (host === "youtube.com" || host === "youtube-nocookie.com") {
    if (u.pathname === "/watch") id = u.searchParams.get("v");
    else {
      const m = u.pathname.match(/^\/(?:shorts|live|embed|v)\/([\w-]{11})/);
      id = m?.[1] ?? null;
    }
  }
  return id && /^[\w-]{11}$/.test(id) ? id : null;
}
