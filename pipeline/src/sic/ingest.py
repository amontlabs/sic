"""Ingest an arbitrary YouTube video: download audio, infer participants, write debate metadata."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import date as Date
from pathlib import Path

from sic import asr, config
from sic.llm import LLM
from sic.models import Debate, Media, Person

ROSTER_SYSTEM = """On te donne le titre, la description et le début de la transcription d'une vidéo politique
(débat, interview, discours). Liste les personnes qui PARLENT dans la vidéo (pas celles dont on parle).
Pour chacune : id (slug court en minuscules sans accents, ex. "lepen"), name, role ("candidate" pour les
responsables politiques, "moderator" pour journalistes/animateurs, "guest" sinon), party (ou null),
aliases (comment on les interpelle : « Madame X », « Monsieur Y »).
Donne aussi un titre court en français pour la vidéo, et "broadcaster" (chaîne ou média) si connu.
Réponds en JSON : {"title": "...", "broadcaster": "...", "roster": [...]}"""


def _yt_dlp(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["yt-dlp", *args], check=True, capture_output=True, text=True)


async def ingest_youtube(video_id: str, debate_id: str, progress=None, llm: LLM | None = None) -> Debate:
    progress = progress or (lambda s, p: None)
    url = f"https://www.youtube.com/watch?v={video_id}"
    raw = config.DATA / "raw" / debate_id
    raw.mkdir(parents=True, exist_ok=True)
    progress("téléchargement", 0.05)
    info_path = raw / "audio.info.json"
    audio_file = next(raw.glob("audio.*[!json]"), None) if raw.exists() else None
    if audio_file is None or not info_path.exists():
        await asyncio.to_thread(_yt_dlp, "-f", "bestaudio[ext=m4a]/bestaudio", "--write-info-json", "--no-playlist", "-o", str(raw / "audio.%(ext)s"), url)
        audio_file = next(p for p in raw.iterdir() if p.name.startswith("audio.") and not p.name.endswith(".json"))
    info = json.loads(info_path.read_text())
    progress("téléchargement", 0.6)
    wav = await asyncio.to_thread(asr.ensure_wav, audio_file, config.CACHE / "audio" / f"{debate_id}.wav")
    progress("téléchargement", 0.8)
    meta = config.DEBATES / f"{debate_id}.json"
    if meta.exists():
        return Debate.model_validate_json(meta.read_text())

    words = await asyncio.to_thread(asr.transcribe, asr.load_audio(wav, 0, 240), 0.0, [])
    excerpt = " ".join(w.text for w in words)[:4000]
    own = llm is None
    llm = llm or LLM()
    try:
        data = await llm.json(
            [
                {"role": "system", "content": ROSTER_SYSTEM},
                {"role": "user", "content": f"Titre : {info.get('title')}\nChaîne : {info.get('channel')}\n"
                 f"Description : {(info.get('description') or '')[:1500]}\n\nDébut de la transcription :\n{excerpt}"},
            ],
            config.LLM_SMART,
        )
    finally:
        if own:
            await llm.aclose()
    roster = []
    seen = set()
    for p in data.get("roster") or []:
        try:
            person = Person.model_validate(p)
        except ValueError:
            continue
        if person.id not in seen:
            seen.add(person.id)
            roster.append(person)
    up = info.get("upload_date") or ""
    d = Date(int(up[:4]), int(up[4:6]), int(up[6:8])) if len(up) == 8 else Date.today()
    debate = Debate(
        id=debate_id,
        title=data.get("title") or info.get("title") or debate_id,
        date=d,
        broadcaster=data.get("broadcaster") or info.get("channel"),
        roster=roster,
        media=Media(youtube_id=video_id, file=None, duration=info.get("duration")),
    )
    meta.write_text(debate.model_dump_json(indent=1))
    progress("téléchargement", 1.0)
    return debate


def audio_path(debate: Debate) -> Path:
    wav = config.CACHE / "audio" / f"{debate.id}.wav"
    if wav.exists():
        return wav
    if debate.media.file:
        return asr.ensure_wav(config.ROOT / debate.media.file, wav)
    raise FileNotFoundError(f"no audio for {debate.id}")
