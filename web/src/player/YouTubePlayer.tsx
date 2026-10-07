import { useEffect, useId, useRef, useState } from "react";
import type { PlayerApi, PlayerCallbacks } from "./types";

// Minimal typing of the YouTube IFrame API we use.
interface YTPlayer {
  getCurrentTime(): number;
  seekTo(t: number, allowSeekAhead: boolean): void;
  playVideo(): void;
  pauseVideo(): void;
  getPlayerState(): number;
  setPlaybackRate(r: number): void;
  mute(): void;
  destroy(): void;
}
interface YTNamespace {
  Player: new (
    el: HTMLElement | string,
    opts: {
      videoId: string;
      host?: string;
      playerVars?: Record<string, string | number>;
      events?: {
        onReady?: () => void;
        onStateChange?: (e: { data: number }) => void;
        onError?: (e: { data: number }) => void;
      };
    },
  ) => YTPlayer;
}
declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let ytPromise: Promise<YTNamespace> | null = null;
function loadYouTubeApi(): Promise<YTNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (ytPromise) return ytPromise;
  ytPromise = new Promise<YTNamespace>((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prev?.();
      if (window.YT) resolve(window.YT);
    };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.async = true;
    s.onerror = () => {
      ytPromise = null;
      reject(new Error("YouTube indisponible"));
    };
    document.head.appendChild(s);
  });
  return ytPromise;
}

interface Props extends PlayerCallbacks {
  videoId: string;
  title: string;
  muted?: boolean;
}

export function YouTubePlayer({ videoId, title, muted, onReady, onTimeUpdate, onPlayingChange, onError }: Props) {
  const hostId = useId().replace(/:/g, "");
  const hostRef = useRef<HTMLDivElement>(null);
  const cbs = useRef({ onReady, onTimeUpdate, onPlayingChange, onError });
  cbs.current = { onReady, onTimeUpdate, onPlayingChange, onError };
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let disposed = false;
    let player: YTPlayer | null = null;
    let poll = 0;
    const mount = document.createElement("div");
    hostRef.current?.appendChild(mount);
    loadYouTubeApi()
      .then((YT) => {
        if (disposed) return;
        player = new YT.Player(mount, {
          videoId,
          host: "https://www.youtube-nocookie.com",
          playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
          events: {
            onReady: () => {
              if (!player || disposed) return;
              if (muted) player.mute();
              const p = player;
              const api: PlayerApi = {
                kind: "youtube",
                getCurrentTime: () => p.getCurrentTime(),
                seek: (t) => {
                  p.seekTo(Math.max(0, t), true);
                  cbs.current.onTimeUpdate?.(t);
                },
                play: () => p.playVideo(),
                pause: () => p.pauseVideo(),
                isPaused: () => p.getPlayerState() !== 1,
                setPlaybackRate: (r) => p.setPlaybackRate(r),
              };
              cbs.current.onReady?.(api);
              poll = window.setInterval(() => cbs.current.onTimeUpdate?.(p.getCurrentTime()), 200);
            },
            onStateChange: (e) => cbs.current.onPlayingChange?.(e.data === 1),
            onError: () => cbs.current.onError?.("La vidéo YouTube n'a pas pu être lue."),
          },
        });
      })
      .catch(() => {
        if (disposed) return;
        setFailed(true);
        cbs.current.onError?.("Le lecteur YouTube n'a pas pu être chargé.");
      });
    return () => {
      disposed = true;
      window.clearInterval(poll);
      try {
        player?.destroy();
      } catch {
        /* already gone */
      }
      mount.remove();
    };
  }, [videoId, muted]);

  return (
    <div className="relative h-full w-full bg-black" aria-label={title}>
      <div id={hostId} ref={hostRef} className="absolute inset-0 [&>iframe]:h-full [&>iframe]:w-full" />
      {failed && (
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-white/80">
          <p>
            Lecteur YouTube indisponible.{" "}
            <a className="underline" href={`https://www.youtube.com/watch?v=${videoId}`} target="_blank" rel="noreferrer">
              Ouvrir sur YouTube
            </a>
          </p>
        </div>
      )}
    </div>
  );
}
