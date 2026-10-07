"""HTTP API for the web app (see docs/API.md).

    uv run python -m sic.server            # http://localhost:8787
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import subprocess
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from sic import config
from sic.models import Event, Run, Verdict, VerdictLabel

log = logging.getLogger("sic.server")


class _RunCache:
    """run.json files keyed by *directory* id (``RUNS/<debate_id>/``).

    The directory id is not always ``run.debate.id``: a ``--minutes 20`` run lives in
    ``RUNS/<id>-20m/`` but its ``debate.id`` is ``<id>``. Always save with the id it was loaded with.
    """

    def __init__(self) -> None:
        self._runs: dict[str, tuple[float, Run]] = {}

    def path(self, debate_id: str) -> Path:
        return config.RUNS / debate_id / "run.json"

    def get(self, debate_id: str) -> Run | None:
        p = self.path(debate_id)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        hit = self._runs.get(debate_id)
        if hit and hit[0] == mtime:
            return hit[1]
        run = Run.model_validate_json(p.read_text())
        self._runs[debate_id] = (mtime, run)
        return run

    def save(self, debate_id: str, run: Run) -> None:
        p = self.path(debate_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(run.model_dump_json())
        tmp.replace(p)
        self._runs[debate_id] = (p.stat().st_mtime, run)


runs = _RunCache()
jobs: dict[str, dict[str, Any]] = {}  # debate_id -> {status, progress, stage, error}


def _valid_id(debate_id: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_\-]{1,80}", debate_id):
        raise HTTPException(400, "invalid id")
    return debate_id


def summary(debate_id: str) -> dict[str, Any] | None:
    meta_path = config.DEBATES / f"{debate_id}.json"
    run = runs.get(debate_id)
    meta = run.debate.model_dump(mode="json") if run else (json.loads(meta_path.read_text()) if meta_path.exists() else None)
    if meta is None:
        return None
    job = jobs.get(debate_id)
    by_label = Counter(v.label for v in run.verdicts) if run else Counter()
    status = "ready" if run else "pending"
    progress = 1.0 if run else 0.0
    if job and job["status"] in ("processing", "error"):
        status, progress = job["status"], job.get("progress", 0.0)
    return {
        "id": debate_id,
        "title": meta.get("title"),
        "date": meta.get("date"),
        "broadcaster": meta.get("broadcaster"),
        "duration": (meta.get("media") or {}).get("duration"),
        "status": status,
        "progress": progress,
        "stage": job.get("stage") if job else None,
        "error": job.get("error") if job else None,
        "counts": {
            "claims": len(run.claims) if run else 0,
            "verdicts": len(run.verdicts) if run else 0,
            "by_label": dict(by_label),
        },
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def cors_origins() -> list[str]:
    raw = os.environ.get("SIC_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip() for o in raw.split(",") if o.strip()]


OPERATOR_HEADER = "X-Sic-Operator"
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "testclient"}


def require_operator(request: Request) -> None:
    """Guard for write routes.

    With ``SIC_OPERATOR_TOKEN`` set, the request must carry ``X-Sic-Operator: <token>`` (401 otherwise).
    Without it (local dev), only clients connecting from localhost are accepted (403 otherwise).
    """
    token = os.environ.get("SIC_OPERATOR_TOKEN", "")
    if token:
        given = request.headers.get(OPERATOR_HEADER, "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "operator token required", headers={"WWW-Authenticate": OPERATOR_HEADER})
        return
    host = request.client.host if request.client else ""
    if host not in _LOCAL_CLIENTS:
        raise HTTPException(403, "write routes are local-only when SIC_OPERATOR_TOKEN is not set")


app = FastAPI(title="Sic API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Range", OPERATOR_HEADER],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"ok": "true"}


@app.get("/api/debates")
async def list_debates() -> list[dict[str, Any]]:
    ids = {p.stem for p in config.DEBATES.glob("*.json")} | {p.parent.name for p in config.RUNS.glob("*/run.json")} | set(jobs)
    out = [s for s in (summary(i) for i in sorted(ids)) if s]
    out.sort(key=lambda s: str(s.get("date") or ""), reverse=True)  # newest first...
    out.sort(key=lambda s: s["status"] != "processing")  # ...with running jobs on top (stable sort)
    return out


@app.get("/api/debates/{debate_id}")
async def get_debate(debate_id: str, events: int = 1) -> JSONResponse:
    run = runs.get(_valid_id(debate_id))
    if run is None:
        meta = config.DEBATES / f"{debate_id}.json"
        if not meta.exists():
            raise HTTPException(404, "debate not found")
        return JSONResponse({"debate": json.loads(meta.read_text()), "segments": [], "claims": [], "verdicts": [], "events": [], "stats": {}})
    data = run.model_dump(mode="json", exclude={"events"} if not events else None)
    for s in data["segments"]:
        s["words"] = []  # keep payload small; the UI only needs segment timing
    return JSONResponse(data)


# -- live -----------------------------------------------------------------------------------------


async def _replay(run: Run, start: float, speed: float, request: Request):
    import time

    t0 = time.monotonic()
    clock = lambda: start + (time.monotonic() - t0) * speed
    yield {"event": "status", "data": json.dumps({"seq": 0, "type": "status", "t": start, "at": start, "data": {"state": "start", "from": start, "speed": speed}})}
    # Catch-up: everything already available at `start` is sent immediately.
    pending = [e for e in run.events if e.at >= start or e.type != "segment" or e.t >= start - 120]
    last_tick = -1.0
    for e in pending:
        while True:
            if await request.is_disconnected():
                return
            now = clock()
            if e.at <= now:
                break
            if now - last_tick >= 1.0:
                last_tick = now
                yield {"event": "status", "data": json.dumps({"seq": 0, "type": "status", "t": now, "at": now, "data": {"state": "clock", "t": now}})}
            await asyncio.sleep(min(0.25, max(0.01, (e.at - now) / speed)))
        yield {"event": e.type, "id": str(e.seq), "data": e.model_dump_json()}
    end = clock()
    yield {"event": "status", "data": json.dumps({"seq": 0, "type": "status", "t": end, "at": end, "data": {"state": "end"}})}


async def _pipeline_stream(debate, wav: Path, start: float, request: Request):
    from sic.library import Library
    from sic.live import LivePipeline
    from sic.llm import LLM
    from sic.sources import build_connectors
    from sic.verify import Verifier

    queue: asyncio.Queue[Event | None] = asyncio.Queue()
    llm = LLM()
    pipe = None
    task: asyncio.Task | None = None

    async def emit(e: Event) -> None:
        await queue.put(e)

    try:
        # Connector construction and the library load read big local indexes: keep them off the loop.
        connectors = await asyncio.to_thread(build_connectors, debate)
        library = await asyncio.to_thread(Library.load)
        pipe = LivePipeline(debate, llm, Verifier(connectors, llm, library), emit)  # verifier: config.LLM_SMART

        async def runner():
            try:
                await pipe.run_file(wav, start=start)
            finally:
                await queue.put(None)

        task = asyncio.create_task(runner())
        while True:
            if await request.is_disconnected():
                break
            try:
                e = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                now = pipe.clock.now()
                yield {"event": "status", "data": json.dumps({"seq": 0, "type": "status", "t": now, "at": now, "data": {"state": "clock", "t": now}})}
                continue
            if e is None:
                break
            yield {"event": e.type, "id": str(e.seq), "data": e.model_dump_json()}
    finally:
        # On client disconnect sse-starlette cancels us: shield the cleanup so it is not cut short.
        with anyio.move_on_after(15, shield=True):
            await _close_pipeline(pipe, task, llm)


async def _close_pipeline(pipe, task: asyncio.Task | None, llm) -> None:
    """Stop everything a pipeline-mode stream started: background verifications, the runner, the LLM client."""
    aclose = getattr(pipe, "aclose", None)
    if aclose is not None:
        try:
            await aclose()
        except Exception:
            log.exception("live pipeline aclose failed")
    if task is not None:
        task.cancel()
        res = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(res, Exception):
            log.error("live pipeline failed", exc_info=res)
    await llm.aclose()


async def _pipeline_source(debate_id: str):
    """Debate + 16 kHz wav for pipeline mode, or a 404 (raised before the SSE response starts)."""
    from sic.ingest import audio_path
    from sic.pipeline import load_debate

    if not (config.DEBATES / f"{debate_id}.json").exists():
        raise HTTPException(404, "debate not found")
    debate = await asyncio.to_thread(load_debate, debate_id)
    try:
        wav = await asyncio.to_thread(audio_path, debate)  # may extract the wav from media.file (ffmpeg)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise HTTPException(404, "no audio for this debate") from e
    return debate, wav


@app.get("/api/debates/{debate_id}/live")
async def live(
    debate_id: str,
    request: Request,
    from_: float = Query(0.0, alias="from", ge=0),
    speed: float = Query(1.0, gt=0, le=100),
    mode: str = "replay",
):
    _valid_id(debate_id)
    if mode == "pipeline":
        debate, wav = await _pipeline_source(debate_id)
        return EventSourceResponse(_pipeline_stream(debate, wav, from_, request), ping=15)
    run = runs.get(debate_id)
    if run is None:
        raise HTTPException(404, "debate not processed yet")
    return EventSourceResponse(_replay(run, from_, speed, request), ping=15)


# -- media ----------------------------------------------------------------------------------------


@app.get("/media/{debate_id}")
async def media(debate_id: str):
    _valid_id(debate_id)
    meta = config.DEBATES / f"{debate_id}.json"
    if not meta.exists():
        raise HTTPException(404, "debate not found")
    f = (json.loads(meta.read_text()).get("media") or {}).get("file")
    path = _media_file(f) if isinstance(f, str) and f else None
    if path is None:
        raise HTTPException(404, "no local media")
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix.lower()])


MEDIA_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".mp3": "audio/mpeg"}


def _media_file(f: str) -> Path | None:
    """Only audio/video files under data/raw/ are ever served (never .env, run files, ...)."""
    raw = (config.DATA / "raw").resolve()
    try:
        path = (config.ROOT / f).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if raw not in path.parents or path.suffix.lower() not in MEDIA_TYPES or not path.is_file():
        return None
    return path


# -- review ---------------------------------------------------------------------------------------


class ReviewIn(BaseModel):
    label: VerdictLabel | None = None
    headline: str | None = None
    explanation: str | None = None
    reviewed: bool = True


NEGATIVE_LABELS = {"faux", "trompeur", "imprecis"}
_FORBIDDEN = re.compile(r"mensonge|menteu(?:r|se)", re.IGNORECASE)


@app.post("/api/debates/{debate_id}/verdicts/{claim_id}", dependencies=[Depends(require_operator)])
async def review(debate_id: str, claim_id: str, body: ReviewIn) -> Verdict:
    run = runs.get(_valid_id(debate_id))
    if run is None:
        raise HTTPException(404, "debate not found")
    for text in (body.headline, body.explanation):
        if text and _FORBIDDEN.search(text):
            raise HTTPException(422, "vocabulaire interdit : Sic ne qualifie jamais une affirmation de « mensonge »")
    for i, v in enumerate(run.verdicts):
        if v.claim_id == claim_id:
            upd = {k: val for k, val in body.model_dump().items() if val is not None}
            nv = v.model_copy(update=upd)
            if nv.label in NEGATIVE_LABELS and not nv.sources:
                raise HTTPException(422, "un verdict négatif doit citer une source")
            run.verdicts[i] = nv
            for e in run.events:
                if e.type == "verdict" and e.data.get("claim_id") == claim_id:
                    e.data = nv.model_dump()
            runs.save(debate_id, run)  # the directory it was loaded from (not run.debate.id)
            return nv
    raise HTTPException(404, "verdict not found")


# -- analyze a link -------------------------------------------------------------------------------

_YT = re.compile(r"(?:youtube\.com/(?:watch\?v=|live/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")


class AnalyzeIn(BaseModel):
    url: str


@app.post("/api/analyze", dependencies=[Depends(require_operator)])
async def analyze(body: AnalyzeIn, background: BackgroundTasks) -> dict[str, str]:
    m = _YT.search(body.url)
    if not m:
        raise HTTPException(400, "Lien YouTube invalide")
    debate_id = f"yt-{m.group(1)}"
    if runs.get(debate_id) is None and jobs.get(debate_id, {}).get("status") != "processing":
        jobs[debate_id] = {"status": "processing", "progress": 0.0, "stage": "téléchargement"}
        background.add_task(_analyze_job, debate_id, m.group(1))
    return {"debate_id": debate_id}


async def _analyze_job(debate_id: str, video_id: str) -> None:
    from sic.ingest import ingest_youtube
    from sic.pipeline import process

    def progress(stage: str, p: float) -> None:
        jobs[debate_id].update(stage=stage, progress=round(p, 3))

    try:
        await ingest_youtube(video_id, debate_id, progress)
        await process(debate_id, strict=False, progress=progress)
        jobs[debate_id] = {"status": "ready", "progress": 1.0, "stage": None}
    except Exception as e:  # surfaced in /api/debates
        log.exception("analyze %s failed", debate_id)
        jobs[debate_id] = {"status": "error", "progress": 0.0, "stage": None, "error": str(e)[:300]}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("sic.server:app", host=os.environ.get("SIC_HOST", "127.0.0.1"), port=int(os.environ.get("SIC_PORT", "8787")))


if __name__ == "__main__":
    main()
