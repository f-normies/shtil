/**
 * Вопрос врача к базе знаний в инспекторе (спека #108, раздел 3.3): состояния панели,
 * тело запроса, отправка с клавиатуры и строки утверждения. Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { sourceText } from "./guidance";

type Answer = Schemas["Answer"];
type Claim = Schemas["Claim"];

/** Предел вопроса, как у ручки (`QUESTION_MAX_LENGTH` в `knowledge/schemas.py`). */
export const QUESTION_MAX = 1000;

export const QUESTION_TEXT = {
  title: "Вопрос к базе знаний",
  intro:
    "Спросите о препаратах, дозах и длительности лечения. Ответ берётся из клинических " +
    "рекомендаций и инструкций в базе знаний, с цитатами.",
  label: "Вопрос по клиническим рекомендациям",
  placeholder: "Например: длительность терапии при ГТР",
  hint: "Без ФИО и других данных пациента",
  asked: "Ваш вопрос",
  waiting: "Готовим ответ…",
  empty: "В базе знаний ответа нет",
  error: "Справка недоступна, повторите позже",
  caption: "Справка по базе знаний. Решение о терапии принимает врач.",
} as const;

export type QuestionKind = "field" | "waiting" | "answer" | "empty" | "error";

/**
 * Запрос вопроса: состояние мутации, отправленный вопрос и ответ. 502, 503 и сбой сети
 * мутация отдаёт одним статусом `error`: текст для врача у них один.
 */
export type QuestionRequest = {
  status: "idle" | "pending" | "success" | "error";
  question?: string;
  data?: Answer;
};

/**
 * Что показать. `asked` — отправленный вопрос над ответом; `intro` — пояснение до первого
 * вопроса; `form` — поле вопроса (в ошибке его нет, только «Повторить»); `locked` — поле
 * и «Спросить» недоступны, пока готовится ответ.
 */
export type QuestionView = {
  kind: QuestionKind;
  asked: string | null;
  intro: boolean;
  notice: string | null;
  claims: Claim[];
  form: boolean;
  locked: boolean;
  retry: boolean;
};

/**
 * Состояние панели по запросу. Ошибка любого рода — 502, 503, сбой сети — одна: справка
 * недоступна. Новый вопрос сразу заменяет прежний ответ ожиданием.
 */
export function questionView(request: QuestionRequest): QuestionView {
  const asked = request.question ?? null;
  const view = (kind: QuestionKind, notice: string | null, claims: Claim[] = []) => ({
    kind,
    asked: kind === "field" ? null : asked,
    intro: kind === "field",
    notice,
    claims,
    form: kind !== "error",
    locked: kind === "waiting",
    retry: kind === "error",
  });
  switch (request.status) {
    case "pending":
      return view("waiting", QUESTION_TEXT.waiting);
    case "error":
      return view("error", QUESTION_TEXT.error);
    case "success": {
      // Ответ без тела (502 прокси) openapi-fetch ошибкой не считает: ответа нет.
      if (!request.data) {
        return view("error", QUESTION_TEXT.error);
      }
      const { claims } = request.data;
      return claims.length > 0 ? view("answer", null, claims) : view("empty", QUESTION_TEXT.empty);
    }
    default:
      return view("field", null);
  }
}

/**
 * Тело `POST /api/v1/knowledge/answers`: вопрос без пробелов по краям и без `source_ids` —
 * поиск по всему корпусу. Пустой или длиннее предела вопрос не отправляется.
 */
export function questionBody(draft: string): { question: string } | null {
  const question = draft.trim();
  return question !== "" && question.length <= QUESTION_MAX ? { question } : null;
}

/** Код клавиши, пока идёт набор IME. Safari заканчивает набор Enter с `isComposing: false`. */
const IME_KEY_CODE = 229;

/** Enter отправляет вопрос, Shift+Enter переносит строку; Enter при наборе IME — нет. */
export function sendsOnKey(event: {
  key: string;
  shiftKey: boolean;
  isComposing: boolean;
  keyCode: number;
}) {
  const composing = event.isComposing || event.keyCode === IME_KEY_CODE;
  return event.key === "Enter" && !event.shiftKey && !composing;
}

/**
 * Документ и раздел под цитатой. У ответа нет списка КР пациента: КР узнаётся по уровням
 * УУР и УДД. Сами уровни под строкой источника выводит `levelsText`.
 */
export function claimSource(claim: Claim): string {
  return sourceText(claim.fragment, []);
}

/** «Ответ, 19.10, 09:52» в часовом поясе браузера (`timeZone` — для тестов). */
export function answeredText(at: number, timeZone?: string): string {
  const format = new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  });
  return `Ответ, ${format.format(new Date(at))}`;
}
