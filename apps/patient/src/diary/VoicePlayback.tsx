import pauseIcon from "@compass/ui/assets/ikonka-pauza.svg";
import playIcon from "@compass/ui/assets/ikonka-vosproizvesti.svg";
import { useEffect, useRef, useState } from "react";

const WAVEFORM = [
  10, 17, 25, 14, 22, 31, 18, 26, 35, 21, 28, 18, 32, 24, 36, 20, 29, 15, 25, 34, 22, 16, 27, 12,
].map((height, slot) => ({ id: `bar-${slot}`, height }));

function formatDuration(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safeSeconds / 60)}:${String(safeSeconds % 60).padStart(2, "0")}`;
}

/** Accessible waveform controls backed by the owner's local or server audio URL. */
export function VoicePlayback({
  src,
  durationMs,
  onDurationChange,
}: {
  src: string;
  durationMs: number;
  onDurationChange?: (durationMs: number) => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const durationChangeRef = useRef(onDurationChange);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(Math.max(1, durationMs / 1000));
  const [playing, setPlaying] = useState(false);
  durationChangeRef.current = onDurationChange;

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "metadata";
    audio.src = src;
    audioRef.current = audio;
    setDuration(Math.max(1, durationMs / 1000));
    setPosition(0);

    const onLoadedMetadata = () => {
      if (!Number.isFinite(audio.duration)) return;
      setDuration(audio.duration);
      durationChangeRef.current?.(Math.round(audio.duration * 1000));
    };
    const onTimeUpdate = () => setPosition(audio.currentTime);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    audio.addEventListener("loadedmetadata", onLoadedMetadata);
    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.pause();
      audio.removeEventListener("loadedmetadata", onLoadedMetadata);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
      audio.removeAttribute("src");
      audioRef.current = null;
    };
  }, [durationMs, src]);

  function seek(nextPosition: number): void {
    const audio = audioRef.current;
    const mediaDuration = audio && Number.isFinite(audio.duration) ? audio.duration : duration;
    const next = Math.min(mediaDuration, Math.max(0, nextPosition));
    if (audio) audio.currentTime = next;
    setPosition(next);
  }

  function seekWithPointer(event: React.MouseEvent<HTMLDivElement>): void {
    const bounds = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
    seek(ratio * duration);
  }

  function seekWithKeyboard(event: React.KeyboardEvent<HTMLDivElement>): void {
    const step = event.shiftKey ? 10 : 1;
    if (event.key === "ArrowLeft") seek(position - step);
    else if (event.key === "ArrowRight") seek(position + step);
    else if (event.key === "Home") seek(0);
    else if (event.key === "End") seek(duration);
    else return;
    event.preventDefault();
  }

  async function togglePlayback(): Promise<void> {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      try {
        await audio.play();
      } catch {
        setPlaying(false);
      }
    } else {
      audio.pause();
    }
  }

  const progress = Math.min(100, (position / duration) * 100);

  return (
    <fieldset className="patient-audio-player">
      <legend className="sr-only">Аудиозапись</legend>
      <button
        type="button"
        className="patient-audio-player__toggle"
        aria-label={playing ? "Приостановить аудиозапись" : "Прослушать аудиозапись"}
        onClick={() => void togglePlayback()}
      >
        <img src={playing ? pauseIcon : playIcon} alt="" />
      </button>
      <div
        className="patient-audio-player__wave"
        role="slider"
        aria-label="Перемотать аудиозапись"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(position)}
        aria-valuetext={`${formatDuration(position)} из ${formatDuration(duration)}`}
        tabIndex={0}
        onClick={seekWithPointer}
        onKeyDown={seekWithKeyboard}
      >
        {WAVEFORM.map(({ id, height }, index) => (
          <i
            key={id}
            data-played={((index + 1) / WAVEFORM.length) * 100 <= progress || undefined}
            style={{ height: `${height}px` }}
          />
        ))}
      </div>
      <time className="patient-audio-player__time">
        {formatDuration(position)} из {formatDuration(duration)}
      </time>
    </fieldset>
  );
}
