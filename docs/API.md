# Sic — HTTP API (server: `pipeline/src/sic/server.py`, default `http://localhost:8787`)

All JSON shapes follow `pipeline/src/sic/models.py` (mirrored in `web/src/types.ts`).

## Debates

`GET /api/debates` → `DebateSummary[]`

```json
[{ "id": "2022-macron-lepen", "title": "...", "date": "2022-04-20", "broadcaster": "TF1 / France 2",
   "duration": 10211.5, "status": "ready" | "processing" | "error",
   "progress": 0.0-1.0, "counts": { "claims": 120, "verdicts": 110, "by_label": { "faux": 9, ... } } }]
```

`GET /api/debates/{id}` → `Run` (`{ debate, segments, claims, verdicts, events, stats }`).
`events` may be omitted (`?events=0`) to keep the payload small.

## Live (simulated)

`GET /api/debates/{id}/live?from=0&speed=1` → `text/event-stream`.
Each SSE message: `event: <Event.type>`, `data: <Event JSON>`.
The server paces events by their `at` field (media-time clock) starting at `from`, at `speed`x.
A `status` event `{ "state": "clock", "t": <media seconds> }` is sent every second so the client can
display the stream clock. Final event: `status` with `{ "state": "end" }`.

`GET /api/debates/{id}/live?mode=pipeline&from=0` runs the *actual* live pipeline (audio fed in real
time → ASR → claims → verification), same event format. Dev only.

## Analyze a link (replay of any video)

`POST /api/analyze` body `{ "url": "https://www.youtube.com/watch?v=..." }` → `{ "debate_id": "yt-<id>" }`.
Progress is visible in `GET /api/debates` (`status`, `progress`); partial results are available from
`GET /api/debates/{id}` while processing.

## Media

`GET /media/{debate_id}` → the local video file (HTTP Range supported). Dev only; in production the
player uses the YouTube embed (`debate.media.youtube_id`).

## Review (operator)

`POST /api/debates/{id}/verdicts/{claim_id}` body `{ "label"?: VerdictLabel, "headline"?: str,
"explanation"?: str, "reviewed": true }` → updated `Verdict`. Marks a verdict as human-reviewed.
