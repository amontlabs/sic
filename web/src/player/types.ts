/** One interface for every video backend (local <video>, YouTube iframe, or a bare clock). */
export interface PlayerApi {
  readonly kind: "file" | "youtube" | "clock";
  getCurrentTime(): number;
  seek(t: number): void;
  play(): void;
  pause(): void;
  isPaused(): boolean;
  setPlaybackRate(rate: number): void;
}

export type PlayerSource =
  | { kind: "file"; src: string }
  | { kind: "youtube"; videoId: string }
  | { kind: "clock"; duration: number };

export interface PlayerCallbacks {
  onReady?: (api: PlayerApi) => void;
  /** throttled (~8 Hz) plus immediate after seeks */
  onTimeUpdate?: (t: number) => void;
  onPlayingChange?: (playing: boolean) => void;
  onError?: (message: string) => void;
}
