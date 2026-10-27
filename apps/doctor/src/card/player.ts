/**
 * «Врач v4/Плеер» голосовой записи дневника (спека #115, раздел 3.5): клавиатура ползунка
 * и сбои воспроизведения. Чистые функции, без React.
 */

const SEEK_SECONDS = 5;

/** ← → — на 5 с, Home и End — к началу и концу записи; другие клавиши не перематывают. */
export function seekTarget(key: string, position: number, duration: number): number | null {
  const targets: Record<string, number> = {
    ArrowLeft: position - SEEK_SECONDS,
    ArrowRight: position + SEEK_SECONDS,
    Home: 0,
    End: duration,
  };
  const target = targets[key];
  return target === undefined ? null : Math.min(Math.max(target, 0), duration);
}

/**
 * Отказ `play()` — сбой, кроме прерывания паузой: пауза приходит, когда врач запускает
 * другую запись, а одновременно играет одна.
 */
export const isPlayFailure = (error: unknown): boolean =>
  !(error instanceof DOMException && error.name === "AbortError");
