import { useEffect, useRef } from "react";
import type { PlayerApi, PlayerCallbacks } from "./types";

interface Props extends PlayerCallbacks {
  src: string;
  title: string;
  muted?: boolean;
  controls?: boolean;
}

export function Html5Player({ src, title, muted, controls = true, onReady, onTimeUpdate, onPlayingChange, onError }: Props) {
  const ref = useRef<HTMLVideoElement>(null);
  const cbs = useRef({ onReady, onTimeUpdate, onPlayingChange, onError });
  cbs.current = { onReady, onTimeUpdate, onPlayingChange, onError };

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    let raf = 0;
    let lastBucket = -1;
    const emit = (force = false) => {
      const t = video.currentTime;
      const bucket = Math.floor(t * 8);
      if (force || bucket !== lastBucket) {
        lastBucket = bucket;
        cbs.current.onTimeUpdate?.(t);
      }
    };
    const loop = () => {
      emit();
      raf = requestAnimationFrame(loop);
    };
    const onPlay = () => {
      cbs.current.onPlayingChange?.(true);
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
    };
    const onPause = () => {
      cbs.current.onPlayingChange?.(false);
      cancelAnimationFrame(raf);
      emit(true);
    };
    const onSeek = () => emit(true);
    const onErr = () => cbs.current.onError?.("La vidéo n'a pas pu être chargée.");
    video.addEventListener("play", onPlay);
    video.addEventListener("pause", onPause);
    video.addEventListener("ended", onPause);
    video.addEventListener("seeking", onSeek);
    video.addEventListener("seeked", onSeek);
    video.addEventListener("timeupdate", onSeek);
    video.addEventListener("error", onErr);

    const api: PlayerApi = {
      kind: "file",
      getCurrentTime: () => video.currentTime,
      seek: (t) => {
        video.currentTime = Math.max(0, t);
        emit(true);
      },
      play: () => {
        void video.play().catch(() => {
          // autoplay with sound refused: retry muted so "Voir l'extrait" still works
          video.muted = true;
          void video.play().catch(() => undefined);
        });
      },
      pause: () => video.pause(),
      isPaused: () => video.paused,
      setPlaybackRate: (r) => {
        video.playbackRate = r;
      },
    };
    cbs.current.onReady?.(api);
    return () => {
      cancelAnimationFrame(raf);
      video.removeEventListener("play", onPlay);
      video.removeEventListener("pause", onPause);
      video.removeEventListener("ended", onPause);
      video.removeEventListener("seeking", onSeek);
      video.removeEventListener("seeked", onSeek);
      video.removeEventListener("timeupdate", onSeek);
      video.removeEventListener("error", onErr);
    };
  }, [src]);

  return (
    <video
      ref={ref}
      src={src}
      title={title}
      aria-label={title}
      controls={controls}
      muted={muted}
      playsInline
      preload="metadata"
      className="block h-full w-full bg-black object-contain"
      data-testid="video"
    />
  );
}
