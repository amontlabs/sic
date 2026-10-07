# Sic — « Ce qu'ils disent, vérifié en direct »

Sic transcribes political debates, identifies who is speaking, detects checkable factual claims and
fact-checks them against official sources — live or on any replay. Built for the French presidential
election (1st round: 18 April 2027). Domain: sic.vote.

> *[sic]* — what journalists write after a quote to say "this is exactly what was said, errors included".

## How it works

```
audio ──► speech-to-text ──► who speaks ──► claim detection ──► verification ──► feed / replay
          whisper-large-v3   voice clusters   GLM 5.3 over a     1. pre-checked library (instant)
          turbo (MLX)        + names from     rolling window      2. agent with tools: INSEE, Eurostat,
                             context (LLM)                           Assemblée votes, web search
```

1. **Speech-to-text** — `mlx-whisper` (large-v3-turbo) on Apple Silicon: 2h50 of debate in ~5 min.
2. **Who speaks** — anonymous voice clusters (sherpa-onnx: pyannote segmentation + speaker embeddings),
   then an LLM names them from context ("Madame Le Pen, à vous…"). **No face recognition and no stored
   voiceprints** — biometric identification is GDPR art. 9 territory.
3. **Claims** — the LLM extracts *checkable* facts (figures, votes, laws, history, quotes) and skips
   opinions and promises. Every claim keeps the verbatim quote, aligned to word timestamps.
4. **Verification** —
   - first the **library**: fact-checks published *before* the debate (politicians repeat themselves);
   - otherwise a tool-using agent: **INSEE** (51k series, publication-lag aware), **Eurostat** (27 datasets),
     **Assemblée nationale** votes since 2012 (18k scrutins, every deputy's position), and **web search**
     (trusted domains first).
   - Product rules enforced in code: no « Faux / Trompeur / Imprécis » without a cited source; low
     confidence → « Invérifiable »; never the word « mensonge ».
5. **Strict live mode** — when replaying an old debate, Sic only uses information available on the
   debate day (data published later, and articles about the debate itself, are filtered out).

## Repository

| Path | What |
|---|---|
| `pipeline/` | Python package `sic` (uv): ASR, diarization, claims, verification, live mode, API server, evaluation |
| `pipeline/src/sic/sources/` | Data connectors: `insee.py`, `eurostat.py`, `assemblee.py`, `web.py` |
| `web/` | React + Vite + Tailwind app: home, replay `/d/:id`, live second screen `/live/:id`, share cards |
| `data/debates/` | Debate metadata (roster, date, media) |
| `data/runs/<id>/` | Processed artifacts (`run.json` is what the app loads) |
| `data/library/` | Pre-debate fact-check library (2022 campaign, 91 entries) |
| `data/gold/` | Published fact-checks of the 2022 debate, used for evaluation (56 claims) |
| `data/eval/` | Evaluation reports |
| `docs/API.md` | HTTP API contract |

## Setup

Requirements: macOS on Apple Silicon (MLX), `uv`, `bun`, `ffmpeg`, `yt-dlp`.

```bash
# secrets: z.ai API key (GLM models + web search). Never committed.
echo "ZAI_API_KEY=..." > .env
# optional, better web citations: BRAVE_API_KEY=...

cd pipeline && uv sync && cd ..
./scripts/fetch_models.sh                                   # speech models (VAD, diarization)
cd pipeline && uv run python -m sic.sources.assemblee build # Assemblée index (~30 s, 115 MB, cached)
```

## Run

```bash
# 1. get the 2022 debate (TF1 replay) — dev only, never redistributed
yt-dlp -f "bv*[height<=720][ext=mp4]+ba[ext=m4a]" --merge-output-format mp4 \
  -o data/raw/debat-2022.mp4 "https://www.youtube.com/watch?v=elgTG9oDiJY"

# 2. process it (each stage is cached and resumable)
cd pipeline && uv run python -m sic.pipeline run 2022-macron-lepen

# 3. API + web app
uv run python -m sic.server            # http://localhost:8787
cd ../web && bun install && bun run dev  # http://localhost:5173
```

- Replay: http://localhost:5173/d/2022-macron-lepen
- Live second screen (simulated from the processed run): http://localhost:5173/live/2022-macron-lepen?speed=4
- Truly live pipeline on the audio, in real time: `GET /api/debates/2022-macron-lepen/live?mode=pipeline`,
  or record a live run with latency stats: `uv run python -m sic.live 2022-macron-lepen --from 600 --minutes 10`
- Any YouTube link: paste it on the home page (downloads audio, infers participants, processes).
- Operator review (mark a verdict as human-checked / correct it): add `?operator=1` to a replay URL.

## Tests

```bash
cd pipeline && uv run pytest -m "not network"     # unit + API tests (no network)
cd pipeline && uv run pytest -m network           # live checks against INSEE / Eurostat / AN
cd web && bun run test && bun run typecheck       # unit/component tests
cd web && bun run e2e                             # Playwright, against a mock API
cd web && bunx playwright test -c playwright.real.config.ts   # Playwright against the real stack
cd pipeline && uv run python -m sic.evaluate 2022-macron-lepen  # compare with published fact-checks
```

## Evaluation

See [`data/eval/2022-macron-lepen.md`](data/eval/2022-macron-lepen.md) for the latest numbers on the
20 April 2022 debate, compared with the claims checked by Le Monde, franceinfo, Libération, AFP,
TF1, 20 Minutes and others.

## Known limitations / next steps

- Web search: z.ai's search sometimes returns homepage-only links; Brave's HTML endpoint is a rate-limited
  fallback. A Brave Search API key (`BRAVE_API_KEY`) fixes citations properly.
- INSEE does not expose data vintages: strict mode filters by estimated publication date, but values are
  the latest revisions (stated in every tool output).
- Verification latency is ~20–40 s per claim with `glm-5.3` (quality first; `glm-5.3-flash` is ~2× faster
  but made wrong calls in tests). Library matches are instant.
- The library (`data/library/`) was compiled from published fact-checks and is marked `reviewed: false`
  until a Sic editor validates entries.
- Live audio input is a file played in real time; plugging a broadcast stream (HLS/DVB) in is the next step.
