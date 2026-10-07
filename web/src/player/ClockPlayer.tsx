import { useEffect, useRef, useState } from "react";
import type { PlayerApi, PlayerCallbacks } from "./types";
import { formatTime } from "../lib/format";
import { IconPause, IconPlay } from "../components/icons";

interface Props extends PlayerCallbacks {
  duration: number;
  title: string;
}

/** No video available: a virtual clock so transcript & claims still sync. */
export function ClockPlayer({ duration, title, onReady, onTimeUpdate, onPlayingChange }: Props) {
  const cbs = useRef({ onReady, onTimeUpdate, onPlayingChange });
  cbs.current = { onReady, onTimeUpdate, onPlayingChange };
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const apiRef = useRef<PlayerApi | null>(null);

  useEffect(() => {
    let time = 0;
    let rate = 1;
    let playingNow = false;
    let raf = 0;
    let last = 0;
    const emit = () => {
      setT(time);
      cbs.current.onTimeUpdate?.(time);
    };
    const tick = (now: number) => {
      time = Math.min(duration, time + ((now - last) / 1000) * rate);
      last = now;
      emit();
      if (time >= duration) api.pause();
      else raf = requestAnimationFrame(tick);
    };
    const api: PlayerApi = {
      kind: "clock",
      getCurrentTime: () => time,
      seek: (s) => {
        time = Math.max(0, Math.min(duration, s));
        emit();
      },
      play: () => {
        if (playingNow) return;
        playingNow = true;
        setPlaying(true);
        cbs.current.onPlayingChange?.(true);
        last = performance.now();
        raf = requestAnimationFrame(tick);
      },
      pause: () => {
        playingNow = false;
        setPlaying(false);
        cbs.current.onPlayingChange?.(false);
        cancelAnimationFrame(raf);
      },
      isPaused: () => !playingNow,
      setPlaybackRate: (r) => {
        rate = r;
      },
    };
    apiRef.current = api;
    cbs.current.onReady?.(api);
    return () => cancelAnimationFrame(raf);
  }, [duration]);

  const pct = duration > 0 ? (t / duration) * 100 : 0;
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-4 bg-ink-950 p-6 text-center text-white" aria-label={title}>
      <p className="max-w-sm text-sm text-white/70">Vidéo indisponible pour ce débat. La transcription reste synchronisée.</p>
      <button
        type="button"
        onClick={() => (playing ? apiRef.current?.pause() : apiRef.current?.play())}
        className="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-semibold text-black focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        {playing ? <IconPause className="size-4" /> : <IconPlay className="size-4" />}
        {playing ? "Pause" : "Lecture"}
      </button>
      <div className="flex w-full max-w-sm items-center gap-3 text-xs tabular-nums text-white/70">
        <span>{formatTime(t)}</span>
        <input
          type="range"
          min={0}
          max={duration}
          step={0.5}
          value={t}
          onChange={(e) => apiRef.current?.seek(Number(e.target.value))}
          className="flex-1 accent-white"
          aria-label="Position de lecture"
          style={{ backgroundSize: `${pct}% 100%` }}
        />
        <span>{formatTime(duration)}</span>
      </div>
    </div>
  );
}
