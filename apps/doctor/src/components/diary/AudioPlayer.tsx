import { LoaderCircle, Pause, Play } from "lucide-react";
import { type KeyboardEvent, type MouseEvent, useRef, useState } from "react";
import { formatDuration } from "../../card/diary";
import { isPlayFailure, seekTarget } from "../../card/player";

/**
 * «Врач v4/Плеер»: `<audio>` со своими кнопками — пуск и пауза, дорожка, длительность.
 * Пробел — пуск и пауза, ← → — перемотка на 5 с, Home и End — начало и конец. Одновременно
 * играет одна запись. Пока звук догружается — признак загрузки; не загрузился —
 * «Не удалось загрузить запись» и «Повторить».
 */
export function AudioPlayer({ url, durationMs }: { url: string; durationMs: number }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [position, setPosition] = useState(0);
  const duration = durationMs / 1000;
  const total = formatDuration(durationMs);
  const progress = duration > 0 ? Math.min(position / duration, 1) : 0;

  function fail() {
    setFailed(true);
    setLoading(false);
    setPlaying(false);
  }

  function toggle() {
    const element = audio.current;
    if (!element) {
      return;
    }
    if (!element.paused) {
      element.pause();
      return;
    }
    for (const other of document.querySelectorAll("audio")) {
      if (other !== element) {
        other.pause();
      }
    }
    setFailed(false);
    element.play().catch((error: unknown) => {
      if (isPlayFailure(error)) {
        fail();
      }
    });
  }

  function retry() {
    setFailed(false);
    audio.current?.load();
  }

  function seekTo(seconds: number) {
    const element = audio.current;
    if (!element) {
      return;
    }
    element.currentTime = seconds;
    setPosition(element.currentTime);
  }

  /** Перемотка — у кнопки и у ползунка; пробел у кнопки — её собственное нажатие. */
  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    const target = seekTarget(event.key, position, duration);
    if (target !== null) {
      event.preventDefault();
      seekTo(target);
    } else if (event.key === " " && event.currentTarget.getAttribute("role") === "slider") {
      event.preventDefault();
      toggle();
    }
  }

  function onTrackClick(event: MouseEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    seekTo(Math.min(Math.max(((event.clientX - box.left) / box.width) * duration, 0), duration));
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex h-8 w-80 max-w-full items-center gap-3">
        {/* biome-ignore lint/a11y/useMediaCaption: расшифровка голоса показана под плеером */}
        <audio
          ref={audio}
          src={url}
          preload="none"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onWaiting={() => setLoading(true)}
          onPlaying={() => setLoading(false)}
          onCanPlay={() => setLoading(false)}
          onError={fail}
          onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
        />
        <button
          type="button"
          onClick={toggle}
          onKeyDown={onKeyDown}
          aria-label={playing ? `Пауза, ${total}` : `Воспроизвести запись, ${total}`}
          aria-busy={loading || undefined}
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary-soft text-primary hover:bg-accent-sky focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          {loading ? (
            <LoaderCircle aria-hidden className="size-4 motion-safe:animate-spin" />
          ) : playing ? (
            <Pause aria-hidden className="size-4 fill-current" />
          ) : (
            <Play aria-hidden className="size-4 fill-current" />
          )}
        </button>
        <div
          role="slider"
          tabIndex={0}
          aria-label="Позиция в записи"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(position)}
          aria-valuetext={`${formatDuration(position * 1000)} из ${total}`}
          onClick={onTrackClick}
          onKeyDown={onKeyDown}
          className="relative flex h-4 flex-1 cursor-pointer items-center rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
        >
          <span className="h-1 w-full rounded-sm bg-line" />
          <span
            className="absolute left-0 h-1 rounded-sm bg-primary"
            style={{ width: `${progress * 100}%` }}
          />
        </div>
        <span className="shrink-0 text-[13px] leading-[18px] text-ink-muted tabular-nums">
          {loading
            ? "Загрузка…"
            : playing || position > 0
              ? formatDuration(position * 1000)
              : total}
        </span>
      </div>
      {failed && (
        <p role="alert" className="flex items-center gap-3 text-[13px] leading-[18px] text-ink">
          Не удалось загрузить запись
          <button
            type="button"
            onClick={retry}
            className="font-semibold text-primary-text underline underline-offset-2 hover:text-primary-hover focus-visible:outline-2 focus-visible:outline-focus"
          >
            Повторить
          </button>
        </p>
      )}
    </div>
  );
}
