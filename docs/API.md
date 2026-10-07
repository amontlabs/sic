# Sic — HTTP API (server: `pipeline/src/sic/server.py`, default `http://localhost:8787`)

All JSON shapes follow `pipeline/src/sic/models.py` (mirrored in `web/src/types.ts`).
Errors are FastAPI JSON: `{ "detail": "..." }` (validation errors: `detail` is a list).

## Access control

- **Read routes** (`GET`) are public.
- **Write routes** (`POST /api/analyze`, `POST /api/debates/{id}/verdicts/{claim_id}`) are operator-only:
  - if the server has `SIC_OPERATOR_TOKEN` set, the request must send the header
    `X-Sic-Operator: <token>` (compared in constant time) → otherwise `401`;
  - if `SIC_OPERATOR_TOKEN` is unset (local dev), only clients connecting from localhost
    (`127.0.0.1` / `::1`) are accepted → otherwise `403`. Behind a reverse proxy every client may look
    local: always set the token when the server is reachable from the network.
- **CORS**: only the origins listed in `SIC_CORS_ORIGINS` (comma-separated, default
  `http://localhost:5173,http://127.0.0.1:5173`) may call the API from a browser; allowed request
  headers are `Content-Type`, `Range` and `X-Sic-Operator`.

## Debates

`GET /api/debates` → `DebateSummary[]`, debates being processed first, then by date (newest first).

```json
[{ "id": "2022-macron-lepen", "title": "...", "date": "2022-04-20", "broadcaster": "TF1 / France 2",
   "duration": 10211.5, "status": "ready" | "processing" | "error",
   "progress": 0.0-1.0, "stage": "transcription" | null, "error": null | "...",
   "counts": { "claims": 120, "verdicts": 110, "by_label": { "faux": 9, ... } } }]
```
(`status` is `"pending"` for a debate with metadata but no processed run and no job.)

`GET /api/debates/{id}` → `Run` (`{ debate, segments, claims, verdicts, events, stats }`).
`events` may be omitted (`?events=0`) to keep the payload small.

## Live (simulated)

`GET /api/debates/{id}/live?from=0&speed=1` → `text/event-stream`.
Each SSE message: `event: <Event.type>`, `data: <Event JSON>`.
The server paces events by their `at` field (media-time clock) starting at `from`, at `speed`x.
A `status` event `{ "state": "clock", "t": <media seconds> }` is sent every second so the client can
display the stream clock. Final event: `status` with `{ "state": "end" }`.

`GET /api/debates/{id}/live?mode=pipeline&from=0` runs the *actual* live pipeline (audio fed in real
time → ASR → claims → verification), same event format. Dev only. The debate and its audio are checked
before the stream starts: unknown debate → `404`, no audio (no cached wav and no local `media.file`) → `404`
(JSON, not an event stream). When the client disconnects, the pipeline, its pending verifications and
its LLM client are stopped.

## Analyze a link (replay of any video)

`POST /api/analyze` (operator, see Access control) body `{ "url": "https://www.youtube.com/watch?v=..." }`
→ `{ "debate_id": "yt-<id>" }`. Errors: `400` not a YouTube link, `401`/`403` not an operator.
Progress is visible in `GET /api/debates` (`status`, `progress`, `stage`, `error`). Results are written
only when processing completes: until then `GET /api/debates/{id}` returns the debate metadata (once the
download step has written it) with empty `segments`/`claims`/`verdicts`, or `404` before that.

## Media

`GET /media/{debate_id}` → the local video/audio file (HTTP Range supported). Dev only; in production the
player uses the YouTube embed (`debate.media.youtube_id`). Only files under `data/raw/` with a media
extension (`.mp4 .webm .mkv .m4a .mp3`) are served; anything else → `404`.

## Review (operator)

`POST /api/debates/{id}/verdicts/{claim_id}` (operator, see Access control) body
`{ "label"?: VerdictLabel, "headline"?: str, "explanation"?: str, "reviewed": true }` → updated `Verdict`.
Marks a verdict as human-reviewed. The edit is saved to the run it was loaded from (`{id}` may be a
partial run such as `<debate>-20m`).

Errors:
- `401` / `403` — not an operator (see Access control);
- `404` — unknown debate or claim;
- `422` — invalid body (e.g. unknown label), or a product rule is violated:
  - the resulting label is `faux`, `trompeur` or `imprecis` and the verdict has no source
    (`"un verdict négatif doit citer une source"`);
  - `headline` or `explanation` contains « mensonge », « menteur » or « menteuse » (any case).
