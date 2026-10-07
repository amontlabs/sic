import type { Debate } from "../types";
import { mediaUrl } from "../lib/api";
import { ClockPlayer } from "./ClockPlayer";
import { Html5Player } from "./Html5Player";
import type { PlayerCallbacks, PlayerSource } from "./types";
import { YouTubePlayer } from "./YouTubePlayer";

export function sourceForDebate(debate: Debate, fallbackDuration: number): PlayerSource {
  if (debate.media.file) return { kind: "file", src: mediaUrl(debate.id) };
  if (debate.media.youtube_id) return { kind: "youtube", videoId: debate.media.youtube_id };
  return { kind: "clock", duration: debate.media.duration ?? fallbackDuration };
}

interface Props extends PlayerCallbacks {
  source: PlayerSource;
  title: string;
  muted?: boolean;
  controls?: boolean;
}

/** Picks the backend; every backend exposes the same PlayerApi via onReady. */
export function MediaPlayer({ source, title, muted, controls, ...cbs }: Props) {
  switch (source.kind) {
    case "file":
      return <Html5Player key={source.src} src={source.src} title={title} muted={muted} controls={controls} {...cbs} />;
    case "youtube":
      return <YouTubePlayer key={source.videoId} videoId={source.videoId} title={title} muted={muted} {...cbs} />;
    case "clock":
      return <ClockPlayer key="clock" duration={source.duration} title={title} {...cbs} />;
  }
}
