import { Button } from "@compass/ui";
import trashIcon from "@compass/ui/assets/ikonka-korzina.svg";
import microphoneIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import pauseIcon from "@compass/ui/assets/ikonka-pauza.svg";
import stopIcon from "@compass/ui/assets/ikonka-stop.svg";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { VoicePlayback } from "./VoicePlayback";

type CaptureState =
  | "idle"
  | "requesting"
  | "recording"
  | "paused"
  | "saving"
  | "saved"
  | "unsupported";

function supportedMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  return (
    ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) =>
      MediaRecorder.isTypeSupported(type),
    ) ?? null
  );
}

function durationLabel(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

const MIC_WAVEFORM_IDS = Array.from({ length: 32 }, (_, slot) => `mic-bar-${slot}`);

/** Capture a real microphone Blob and hand it to the caller only after MediaRecorder stops. */
export function VoiceCapture({
  onComplete,
  onRecordingChange,
  onSavedBlob,
  onDurationKnown,
  onDelete,
  allowPause = false,
  allowDelete = false,
  autoStart = false,
  savedBlob,
  savedDurationMs,
  compact = false,
}: {
  onComplete: (blob: Blob, durationMs: number) => Promise<void>;
  onRecordingChange?: (recording: boolean) => void;
  onSavedBlob?: (blob: Blob, durationMs: number) => void;
  onDurationKnown?: (durationMs: number) => void;
  onDelete?: () => void;
  allowPause?: boolean;
  allowDelete?: boolean;
  autoStart?: boolean;
  savedBlob?: Blob | null;
  savedDurationMs?: number | null;
  compact?: boolean;
}) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const segmentStartedAtRef = useRef(0);
  const accumulatedMsRef = useRef(0);
  const pausedRef = useRef(false);
  const intervalRef = useRef<number | null>(null);
  const meterFrameRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const discardRecordingRef = useRef(false);
  const autoStartedRef = useRef(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [state, setState] = useState<CaptureState>(
    supportedMimeType() === null ? "unsupported" : "idle",
  );
  const [elapsedMs, setElapsedMs] = useState(savedDurationMs ?? 0);
  const [error, setError] = useState<string | null>(null);
  const [waveform, setWaveform] = useState<number[]>(() =>
    Array.from({ length: 32 }, (_, index) => 6 + ((index * 11 + 7) % 17)),
  );
  const mimeType = supportedMimeType();
  const audioUrl = useMemo(() => (savedBlob ? URL.createObjectURL(savedBlob) : null), [savedBlob]);

  useEffect(() => {
    setElapsedMs(savedDurationMs ?? 0);
  }, [savedDurationMs]);

  useEffect(
    () => () => {
      if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
      if (meterFrameRef.current !== null) cancelAnimationFrame(meterFrameRef.current);
      void audioContextRef.current?.close();
      streamRef.current?.getTracks().forEach((track) => {
        track.stop();
      });
    },
    [],
  );

  useEffect(
    () => () => {
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    },
    [audioUrl],
  );

  useEffect(() => {
    const dialog = deleteDialogRef.current;
    if (!dialog) return;
    if (confirmDelete && !dialog.open) dialog.showModal();
    if (!confirmDelete && dialog.open) dialog.close();
  }, [confirmDelete]);

  const stopMeter = useCallback((): void => {
    if (meterFrameRef.current !== null) cancelAnimationFrame(meterFrameRef.current);
    meterFrameRef.current = null;
  }, []);

  const startMeter = useCallback((): void => {
    const analyser = analyserRef.current;
    if (!analyser) return;
    const bins = new Uint8Array(analyser.frequencyBinCount);
    let lastUpdate = 0;
    const draw = (now: number) => {
      analyser.getByteFrequencyData(bins);
      if (now - lastUpdate >= 100) {
        const bars = Array.from({ length: 32 }, (_, index) => {
          const magnitude =
            bins[Math.min(bins.length - 1, Math.floor((index / 32) * bins.length))] ?? 0;
          return Math.max(6, Math.round((magnitude / 255) * 28));
        });
        setWaveform(bars);
        lastUpdate = now;
      }
      meterFrameRef.current = requestAnimationFrame(draw);
    };
    meterFrameRef.current = requestAnimationFrame(draw);
  }, []);

  const startRecording = useCallback(async (): Promise<void> => {
    setError(null);
    onRecordingChange?.(true);
    setState("requesting");
    if (!mimeType || !navigator.mediaDevices?.getUserMedia) {
      setState("unsupported");
      onRecordingChange?.(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      accumulatedMsRef.current = 0;
      pausedRef.current = false;
      segmentStartedAtRef.current = performance.now();
      const audioContext = new AudioContext();
      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 64;
      audioContext.createMediaStreamSource(stream).connect(analyser);
      audioContextRef.current = audioContext;
      analyserRef.current = analyser;
      await audioContext.resume();
      const recorder = new MediaRecorder(stream, { mimeType });
      recorderRef.current = recorder;
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
        intervalRef.current = null;
        stopMeter();
        stream.getTracks().forEach((track) => {
          track.stop();
        });
        streamRef.current = null;
        const elapsed =
          accumulatedMsRef.current +
          (pausedRef.current ? 0 : performance.now() - segmentStartedAtRef.current);
        const duration = Math.min(600_000, Math.max(1, Math.round(elapsed)));
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || mimeType });
        setElapsedMs(duration);
        void audioContextRef.current?.close();
        audioContextRef.current = null;
        analyserRef.current = null;
        if (discardRecordingRef.current) {
          discardRecordingRef.current = false;
          chunksRef.current = [];
          setState("idle");
          onRecordingChange?.(false);
          return;
        }
        setState("saving");
        void onComplete(blob, duration)
          .then(() => {
            setState("saved");
            onSavedBlob?.(blob, duration);
            onRecordingChange?.(false);
          })
          .catch(() => {
            setState("idle");
            setError("Не удалось сохранить запись на устройстве. Повторите попытку.");
            onRecordingChange?.(false);
          });
      });
      recorder.start(250);
      setState("recording");
      intervalRef.current = window.setInterval(() => {
        const elapsed = accumulatedMsRef.current + performance.now() - segmentStartedAtRef.current;
        setElapsedMs(elapsed);
        if (elapsed >= 600_000) recorder.stop();
      }, 250);
      startMeter();
    } catch {
      streamRef.current?.getTracks().forEach((track) => {
        track.stop();
      });
      streamRef.current = null;
      void audioContextRef.current?.close();
      audioContextRef.current = null;
      analyserRef.current = null;
      setState("unsupported");
      onRecordingChange?.(false);
      setError("Нет доступа к микрофону. Можно продолжить текстом.");
    }
  }, [mimeType, onComplete, onRecordingChange, onSavedBlob, startMeter, stopMeter]);

  useEffect(() => {
    if (!autoStart || autoStartedRef.current) return;
    autoStartedRef.current = true;
    void startRecording();
  }, [autoStart, startRecording]);

  function stopRecording(): void {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording" || recorder?.state === "paused") recorder.stop();
  }

  function togglePause(): void {
    const recorder = recorderRef.current;
    if (recorder?.state === "recording") {
      accumulatedMsRef.current += performance.now() - segmentStartedAtRef.current;
      pausedRef.current = true;
      recorder.pause();
      if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
      intervalRef.current = null;
      stopMeter();
      setState("paused");
    } else if (recorder?.state === "paused") {
      pausedRef.current = false;
      segmentStartedAtRef.current = performance.now();
      recorder.resume();
      setState("recording");
      intervalRef.current = window.setInterval(() => {
        const elapsed = accumulatedMsRef.current + performance.now() - segmentStartedAtRef.current;
        setElapsedMs(elapsed);
        if (elapsed >= 600_000) recorder.stop();
      }, 250);
      startMeter();
    }
  }

  function confirmDiscard(): void {
    setConfirmDelete(false);
    const recorder = recorderRef.current;
    if (recorder?.state === "recording" || recorder?.state === "paused") {
      discardRecordingRef.current = true;
      recorder.stop();
      return;
    }
    chunksRef.current = [];
    setElapsedMs(0);
    setState("idle");
    onDelete?.();
  }

  return (
    <div
      className={`patient-voice-capture${compact ? " patient-voice-capture--compact" : ""}`}
      data-capture-state={state}
    >
      {state === "recording" || state === "paused" ? (
        <>
          <p className="patient-voice-status" role="status">
            <i data-paused={state === "paused" || undefined} />
            {state === "paused" ? "Пауза" : "Идёт запись"}
          </p>
          <p className="patient-voice-duration">
            <span className="sr-only">Длительность записи </span>
            {durationLabel(elapsedMs)}
          </p>
          <div
            className="patient-voice-wave"
            role="img"
            aria-label={state === "paused" ? "Запись на паузе" : "Уровень звука с микрофона"}
          >
            {waveform.map((height, slot) => (
              <i key={MIC_WAVEFORM_IDS[slot]} style={{ height: `${height}px` }} />
            ))}
          </div>
          <div className="patient-voice-controls">
            {allowPause && (
              <Button variant="secondary" onClick={togglePause}>
                <img src={pauseIcon} alt="" />
                {state === "paused" ? "Продолжить" : "Пауза"}
              </Button>
            )}
            <button
              type="button"
              className="patient-record-button patient-record-button--active"
              aria-label="Остановить запись"
              onClick={stopRecording}
            >
              <span className="patient-record-button__inner">
                <img src={stopIcon} alt="" />
              </span>
              <span>{allowPause ? "Готово" : "Остановить запись"}</span>
            </button>
            {allowDelete && (
              <Button variant="secondary" onClick={() => setConfirmDelete(true)}>
                <img src={trashIcon} alt="" />
                Удалить
              </Button>
            )}
          </div>
        </>
      ) : state === "saving" ? (
        <p className="patient-voice-status" role="status">
          Сохраняем аудио на устройстве…
        </p>
      ) : state === "requesting" ? (
        <p className="patient-voice-status" role="status">
          Подключаем микрофон…
        </p>
      ) : savedBlob ? (
        <>
          <VoicePlayback
            src={audioUrl ?? ""}
            durationMs={savedDurationMs ?? elapsedMs}
            onDurationChange={(duration) => {
              if (savedDurationMs == null) {
                setElapsedMs(duration);
                onDurationKnown?.(duration);
              }
            }}
          />
          <p className="patient-voice-status" role="status">
            Голосовая запись сохранена на устройстве · {durationLabel(savedDurationMs ?? elapsedMs)}
          </p>
        </>
      ) : (
        <>
          <button
            type="button"
            className="patient-record-button"
            onClick={() => void startRecording()}
          >
            <span className="patient-record-button__inner">
              <img src={microphoneIcon} alt="" />
            </span>
            <span>{state === "unsupported" ? "Микрофон недоступен" : "Начать запись"}</span>
          </button>
          <p className="patient-voice-status">
            {state === "unsupported" ? "Можно продолжить текстом." : "Нажмите и говорите"}
          </p>
        </>
      )}
      {error && (
        <p className="patient-inline-error" role="alert">
          {error}
        </p>
      )}
      <dialog
        ref={deleteDialogRef}
        className="patient-confirm-dialog"
        role="alertdialog"
        aria-labelledby="voice-delete-title"
        onCancel={(event) => {
          event.preventDefault();
          setConfirmDelete(false);
        }}
        onClose={() => setConfirmDelete(false)}
      >
        <h2 id="voice-delete-title" className="type-patient-title">
          Удалить запись?
        </h2>
        <p className="patient-body-small">
          Она пропадёт из дневника. Удаление аудиозаписи нельзя отменить.
        </p>
        <div className="patient-confirm-dialog__actions">
          <Button variant="secondary" onClick={() => setConfirmDelete(false)}>
            Оставить
          </Button>
          <Button onClick={confirmDiscard}>Удалить</Button>
        </div>
      </dialog>
    </div>
  );
}
