/**
 * Истории болезни в карточке (спека #30, раздел 6; кадр 07в): статусы документов, тексты
 * ошибок загрузки и опрос списка. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";

type DocumentOut = Schemas["DocumentOut"];

/** Ограничения загрузки — раздел 2 спеки, «Входы». */
export const LIMITS_HINT = "PDF, JPEG или PNG, до 10 МБ, до 6 страниц";
export const ACCEPT = "application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png";
const POLL_MS = 5_000;
// Предложение дизайна (кадр 07в): дольше 10 минут — «Разбор идёт дольше обычного».
const LONG_MS = 10 * 60_000;

export type StatusView = {
  text: string;
  tone: "work" | "done" | "failed";
  /** Документ в работе дольше обычного. */
  long: boolean;
  retry: boolean;
};

const FAILURES: Record<string, string> = {
  empty_text: "в документе не найден текст",
  text_too_long: "текст длиннее 15 000 знаков",
  parse_failed: "текст не распознан",
  extract_failed: "данные не извлечены",
};

const UPLOAD_ERRORS: Record<string, string> = {
  document_too_large: "файл больше 10 МБ",
  unsupported_document_type: "подходят PDF, JPEG и PNG",
  too_many_pages: "в документе больше 6 страниц",
  unreadable_document: "файл не открывается",
  invalid_request: "файл не отправился, выберите его ещё раз",
  storage_unavailable: "хранилище недоступно, попробуйте позже",
};

const KINDS: Record<string, string> = {
  "application/pdf": "PDF",
  "image/jpeg": "JPEG",
  "image/png": "PNG",
};

export const OCR_NOTE = "Текст распознан с изображения: названия могли исказиться";

export function isWorking(document: DocumentOut): boolean {
  return document.status === "parsing" || document.status === "extracting";
}

/** Пока документ в работе, список опрашивается раз в 5 с (раздел 2, «Статус»). */
export function pollInterval(documents: readonly DocumentOut[] | undefined): number | false {
  return documents?.some(isWorking) ? POLL_MS : false;
}

/** Документ дошёл до `done` между двумя ответами: у него появились предложения. */
export function finishedDocuments(
  before: readonly DocumentOut[] | undefined,
  after: readonly DocumentOut[],
): boolean {
  if (!before) {
    return false;
  }
  const previous = new Map(before.map((d) => [d.id, d.status]));
  return after.some((d) => d.status === "done" && previous.get(d.id) !== "done");
}

export function statusView(document: DocumentOut, now: Date): StatusView {
  if (document.status === "failed") {
    return {
      text: `Не удалось: ${failureReason(document)}`,
      tone: "failed",
      long: false,
      retry: true,
    };
  }
  if (document.status === "done") {
    return { text: "Готово", tone: "done", long: false, retry: false };
  }
  const extracting = document.status === "extracting";
  const since = extracting ? (document.parsed_at ?? document.created_at) : document.created_at;
  return {
    text: extracting ? "Извлекаются данные" : "Распознаётся",
    tone: "work",
    long: now.getTime() - new Date(since).getTime() > LONG_MS,
    retry: false,
  };
}

function failureReason(document: DocumentOut): string {
  if (document.error_code === "page_unreadable") {
    return document.error_page === null
      ? "страница не читается"
      : `страница ${document.error_page} не читается`;
  }
  return FAILURES[document.error_code ?? ""] ?? "ошибка обработки";
}

/** Строка под именем файла: дата документа, когда загружен, вид и страницы. */
export function documentMeta(document: DocumentOut, now: Date): string {
  const pages = `${KINDS[document.content_type] ?? document.content_type}, ${document.pages} ${plural(
    document.pages,
    ["страница", "страницы", "страниц"],
  )}`;
  const uploaded = `загружен ${whenText(document.created_at, now)}`;
  // Дату документа извлекает модель: до конца разбора её ещё нет.
  const parts =
    document.status === "done"
      ? [
          document.document_date
            ? `Документ от ${dayText(document.document_date)}`
            : "Дата документа не указана",
          uploaded,
        ]
      : [capitalize(uploaded)];
  const meta = [...parts, pages].join(" · ");
  return document.ocr ? `${meta}. ${OCR_NOTE}` : meta;
}

/** «сегодня, 10:46», «21.10» в этом году, «30.12.2025» — раньше. */
export function whenText(iso: string, now: Date): string {
  const date = new Date(iso);
  if (sameDay(date, now)) {
    return `сегодня, ${clock(date)}`;
  }
  const day = `${pad(date.getDate())}.${pad(date.getMonth() + 1)}`;
  return date.getFullYear() === now.getFullYear() ? day : `${day}.${date.getFullYear()}`;
}

/** Время решения: «10:51» сегодня, иначе как `whenText`. */
export function momentText(iso: string, now: Date): string {
  const date = new Date(iso);
  return sameDay(date, now) ? clock(date) : whenText(iso, now);
}

/** Дата без времени из API («2026-09-12») → «12.09.2026», без сдвига часового пояса. */
export function dayText(isoDate: string): string {
  const [year, month, day] = isoDate.split("-");
  return `${day}.${month}.${year}`;
}

/** Форма слова для числа: «1 страница», «3 страницы», «5 страниц». */
export function plural(n: number, [one, few, many]: readonly [string, string, string]): string {
  const tens = n % 100;
  const last = n % 10;
  if (tens >= 11 && tens <= 14) {
    return many;
  }
  if (last === 1) {
    return one;
  }
  return last >= 2 && last <= 4 ? few : many;
}

/** Причина отказа в загрузке. Тело ошибки — RFC 9457 с полем `code`. */
export function uploadErrorText(error: unknown): string {
  const { status, code } = problemOf(error);
  const text =
    UPLOAD_ERRORS[code ?? ""] ?? (status === 413 ? UPLOAD_ERRORS.document_too_large : null);
  return text ?? "файл не загружен, проверьте связь и попробуйте ещё раз";
}

export function retryErrorText(error: unknown): string {
  return problemOf(error).code === "document_not_failed"
    ? "документ уже обрабатывается"
    : "повтор не запущен, проверьте связь и попробуйте ещё раз";
}

/** Выбранный документ или самый новый, если выбора нет или документ исчез. */
export function selectedDocument(
  documents: readonly DocumentOut[],
  id: string | undefined,
): DocumentOut | undefined {
  return documents.find((d) => d.id === id) ?? documents[0];
}

function problemOf(error: unknown): { status?: number; code?: string } {
  if (typeof error !== "object" || error === null) {
    return {};
  }
  const { status, code } = error as { status?: unknown; code?: unknown };
  return {
    status: typeof status === "number" ? status : undefined,
    code: typeof code === "string" ? code : undefined,
  };
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const clock = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
