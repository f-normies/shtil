import { useCallback, useEffect, useRef, useState } from "react";
import { ChatIcon } from "./ChatIcons";
import { VoiceMessagePlayer } from "./VoiceMessagePlayer";

const MAX_DURATION_SECONDS = 600;
const MAX_AUDIO_BYTES = 25 * 1024 * 1024;
const SUPPORTED_TYPES = ["audio/webm;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"];

type RecorderState = "idle" | "requesting" | "recording" | "stopping" | "preview";

export interface VoiceRecorderProps {
  onSend: (audio: Blob) => void | Promise<void>;
  disabled?: boolean;
  doctorMode?: boolean;
  triggerHidden?: boolean;
}

function stopStream(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => {
    track.stop();
  });
}

function getRecorderType(): string | null {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return null;
  return SUPPORTED_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

/** Записывает короткое голосовое и передаёт Blob после явного подтверждения пользователя. */
export function VoiceRecorder({
  onSend,
  disabled = false,
  doctorMode = false,
  triggerHidden = false,
}: VoiceRecorderProps) {
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [previewUrl, setPreviewUrl] = useState("");
  const [sending, setSending] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const intervalRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const requestTokenRef = useRef(0);
  const mountedRef = useRef(false);
  const cancelledRecorderRef = useRef<MediaRecorder | null>(null);
  const limitMessageRef = useRef<string | null>(null);
  const previewRef = useRef<Blob | null>(null);
  const previewUrlRef = useRef("");

  const stopTimer = useCallback(() => {
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const releaseStream = useCallback(() => {
    stopStream(streamRef.current);
    streamRef.current = null;
  }, []);

  const revokePreview = useCallback(() => {
    if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = "";
    previewRef.current = null;
    setPreviewUrl("");
  }, []);

  const stopRecording = useCallback(
    (limitMessage?: string) => {
      if (limitMessage) limitMessageRef.current = limitMessage;
      stopTimer();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        if (mountedRef.current) setState("stopping");
        recorder.stop();
      } else {
        releaseStream();
        if (mountedRef.current) setState("idle");
      }
    },
    [releaseStream, stopTimer],
  );

  const beginRecording = async () => {
    if (state === "recording") {
      stopRecording();
      return;
    }
    if (state !== "idle" || disabled) return;

    const requestToken = requestTokenRef.current + 1;
    requestTokenRef.current = requestToken;
    setError(null);
    setState("requesting");

    try {
      const mediaDevices = navigator.mediaDevices;
      if (!mediaDevices?.getUserMedia) throw new Error("unsupported-media");

      const stream = await mediaDevices.getUserMedia({ audio: true });
      if (!mountedRef.current || requestTokenRef.current !== requestToken) {
        stopStream(stream);
        return;
      }

      const mimeType = getRecorderType();
      if (!mimeType) {
        stopStream(stream);
        throw new Error("unsupported-recorder");
      }

      streamRef.current = stream;
      const recorder = new MediaRecorder(stream, { mimeType });
      recorderRef.current = recorder;
      chunksRef.current = [];
      limitMessageRef.current = null;
      startedAtRef.current = Date.now();
      let byteCount = 0;

      recorder.ondataavailable = (event) => {
        if (event.data.size === 0) return;
        chunksRef.current.push(event.data);
        byteCount += event.data.size;
        if (byteCount > MAX_AUDIO_BYTES) {
          stopRecording("Запись достигла предела 25 МиБ. Её не удалось сохранить.");
        }
      };
      recorder.onerror = () => {
        stopTimer();
        releaseStream();
        recorderRef.current = null;
        if (mountedRef.current) {
          setState("idle");
          setError("Запись прервалась. Можно попробовать ещё раз или отправить текст.");
        }
      };
      recorder.onstop = () => {
        stopTimer();
        releaseStream();
        if (!mountedRef.current || cancelledRecorderRef.current === recorder) {
          if (cancelledRecorderRef.current === recorder) cancelledRecorderRef.current = null;
          return;
        }
        recorderRef.current = null;
        if (limitMessageRef.current) {
          setState("idle");
          setError(limitMessageRef.current);
          return;
        }
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType });
        if (blob.size === 0) {
          setState("idle");
          setError("В записи нет звука. Попробуйте записать ещё раз.");
          return;
        }
        const url = URL.createObjectURL(blob);
        previewRef.current = blob;
        previewUrlRef.current = url;
        setPreviewUrl(url);
        setState("preview");
      };

      recorder.start(1000);
      setState("recording");
      intervalRef.current = window.setInterval(() => {
        const nextElapsed = Math.floor((Date.now() - startedAtRef.current) / 1000);
        setElapsed(nextElapsed);
        if (nextElapsed >= MAX_DURATION_SECONDS) {
          stopRecording("Запись длилась 10 минут. Лимит записи достигнут.");
        }
      }, 250);
    } catch {
      if (requestTokenRef.current !== requestToken || !mountedRef.current) return;
      releaseStream();
      setState("idle");
      setError("Не удалось получить доступ к микрофону. Можно отправить текстовое сообщение.");
    }
  };

  const cancel = useCallback(() => {
    requestTokenRef.current += 1;
    stopTimer();
    const recorder = recorderRef.current;
    if (recorder) {
      cancelledRecorderRef.current = recorder;
      if (recorder.state !== "inactive") recorder.stop();
    }
    recorderRef.current = null;
    releaseStream();
    revokePreview();
    setError(null);
    setElapsed(0);
    setState("idle");
  }, [releaseStream, revokePreview, stopTimer]);

  const sendPreview = async () => {
    const blob = previewRef.current;
    if (!blob || sending || disabled) return;
    setSending(true);
    setError(null);
    try {
      await onSend(blob);
      if (mountedRef.current) {
        revokePreview();
        setElapsed(0);
        setState("idle");
      }
    } catch {
      if (mountedRef.current) setError("Не удалось сохранить голосовое. Запись можно повторить.");
    } finally {
      if (mountedRef.current) setSending(false);
    }
  };

  useEffect(() => {
    if (!disabled) return;
    if (state === "requesting") cancel();
    else if (state === "recording") stopRecording();
  }, [cancel, disabled, state, stopRecording]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestTokenRef.current += 1;
      stopTimer();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        cancelledRecorderRef.current = recorder;
        recorder.stop();
      }
      stopStream(streamRef.current);
      streamRef.current = null;
      if (previewUrlRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = "";
      previewRef.current = null;
    };
  }, [stopTimer]);

  const buttonLabel =
    state === "recording" || state === "stopping" ? "Остановить запись" : "Начать запись";
  return (
    <div className={`ui-chat-recorder${doctorMode ? " ui-chat-recorder--doctor" : ""}`}>
      <button
        type="button"
        className={`ui-chat-recorder__trigger${triggerHidden && state === "idle" ? " ui-chat-recorder__trigger--hidden" : ""}`}
        aria-label={buttonLabel}
        disabled={disabled || state === "requesting" || state === "stopping" || state === "preview"}
        onClick={() => void beginRecording()}
      >
        <ChatIcon name={state === "recording" ? "stop" : "mic"} />
        {doctorMode && <span>{state === "recording" ? "Остановить" : "Голосовое"}</span>}
      </button>
      {state === "requesting" && (
        <span className="ui-chat-recorder__status" role="status">
          Запрашиваем доступ к микрофону…
        </span>
      )}
      {state === "recording" && (
        <div className="ui-chat-recorder__recording" role="status">
          <span className="ui-chat-recorder__live-dot" aria-hidden="true" />
          <span>
            Идёт запись · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
          </span>
          <button type="button" className="ui-chat-recorder__stop" onClick={() => stopRecording()}>
            Остановить
          </button>
          <button
            type="button"
            className="ui-chat-recorder__cancel"
            aria-label="Отменить запись"
            onClick={cancel}
          >
            Отменить
          </button>
        </div>
      )}
      {state === "stopping" && (
        <span className="ui-chat-recorder__status" role="status">
          Завершаем запись…
        </span>
      )}
      {state === "preview" && (
        <div className="ui-chat-recorder__preview">
          <VoiceMessagePlayer src={previewUrl} label="Прослушать запись перед отправкой" />
          <button
            type="button"
            className="ui-chat-recorder__send"
            disabled={disabled || sending}
            onClick={() => void sendPreview()}
          >
            {sending ? "Сохраняем…" : "Отправить голосовое"}
          </button>
          <button
            type="button"
            className="ui-chat-recorder__cancel"
            disabled={sending}
            onClick={cancel}
          >
            Удалить запись
          </button>
        </div>
      )}
      {error && (
        <p className="ui-chat-recorder__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
