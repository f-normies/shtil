import { useRef, useState } from "react";
import { ChatIcon } from "./ChatIcons";

export interface VoiceMessagePlayerProps {
  src: string;
  label?: string;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

/** Плеер использует переданный авторизованный адрес или локальный blob URL. */
export function VoiceMessagePlayer({
  src,
  label = "Голосовое сообщение",
}: VoiceMessagePlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [error, setError] = useState(false);

  const togglePlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      void audio.play().catch(() => {
        setPlaying(false);
        setError(true);
      });
      return;
    }
    audio.pause();
  };

  const seek = (value: string) => {
    const audio = audioRef.current;
    const nextTime = Number(value);
    if (audio && Number.isFinite(nextTime)) {
      audio.currentTime = nextTime;
      setCurrentTime(nextTime);
    }
  };

  return (
    <fieldset className="ui-chat-player">
      <legend className="ui-visually-hidden">{label}</legend>
      {/* biome-ignore lint/a11y/useMediaCaption: Sent-message transcripts render as adjacent text; pre-send recordings have no transcript yet. */}
      <audio
        ref={audioRef}
        className="ui-chat-player__audio"
        preload="metadata"
        src={src}
        onDurationChange={(event) => setDuration(event.currentTarget.duration || 0)}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => {
          setError(false);
          setPlaying(true);
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setCurrentTime(0);
        }}
        onError={() => {
          setPlaying(false);
          setError(true);
        }}
      />
      <button
        type="button"
        className="ui-chat-player__toggle"
        aria-label={
          playing ? "Приостановить голосовое сообщение" : "Воспроизвести голосовое сообщение"
        }
        onClick={togglePlayback}
      >
        <ChatIcon name={playing ? "pause" : "play"} />
      </button>
      <span className="ui-chat-player__wave" aria-hidden="true">
        {[4, 9, 13, 7, 16, 11, 6, 14, 8, 12, 5, 10].map((height) => (
          <span key={height} style={{ height }} />
        ))}
      </span>
      <span className="ui-chat-player__time" aria-live="off">
        {formatTime(currentTime)} / {formatTime(duration)}
      </span>
      <label className="ui-chat-player__seek-label">
        <span className="ui-visually-hidden">Перемотка голосового сообщения</span>
        <input
          type="range"
          min="0"
          max={Math.max(duration, 0.1)}
          step="0.25"
          value={Math.min(currentTime, duration || 0)}
          aria-valuetext={`${formatTime(currentTime)} из ${formatTime(duration)}`}
          disabled={duration <= 0}
          onChange={(event) => seek(event.currentTarget.value)}
        />
      </label>
      {error && (
        <span className="ui-chat-player__error" role="alert">
          Не удалось воспроизвести запись.
        </span>
      )}
    </fieldset>
  );
}
