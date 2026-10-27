import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import {
  answeredText,
  claimSource,
  QUESTION_MAX,
  QUESTION_TEXT,
  type QuestionKind,
  type QuestionRequest,
  questionBody,
  questionView,
  sendsOnKey,
} from "./question";

type Fragment = Schemas["FragmentRef"];
type Claim = Schemas["Claim"];

const KR: Fragment = {
  id: "00000000-0000-0000-0000-000000000001",
  key: "kr-gad-2024/doc_3/4",
  source_id: "kr-gad-2024",
  title: "Генерализованное тревожное расстройство",
  version: "457_3",
  section: "doc_3",
  section_title: "3.1. Медикаментозное лечение",
  uur: "A",
  udd: 1,
  status: "active",
};
const LABEL: Fragment = {
  ...KR,
  id: "00000000-0000-0000-0000-000000000002",
  key: "aviandr-ohlp/4.2/1",
  source_id: "aviandr-ohlp",
  title: "АВИАНДР (маритупирдин): общая характеристика лекарственного препарата",
  section: "4.2",
  section_title: "Режим дозирования и способ применения",
  uur: null,
  udd: null,
};
const CLAIM: Claim = {
  statement: "Препараты первой линии при ГТР — антидепрессанты группы СИОЗС.",
  quote: "Рекомендуется назначение антидепрессантов из группы СИОЗС",
  fragment: KR,
};
const ASKED = "Какие препараты первой линии при ГТР?";

const answer = (claims: Claim[]): Schemas["Answer"] => ({ claims, dropped: 0, run_id: "r1" });

// Состояния раздела 3.3 спеки #108: вид, текст пометки, поле и «Повторить».
describe("questionView: states of spec #108, section 3.3", () => {
  const rows: [string, QuestionRequest, QuestionKind, string | null, boolean, boolean][] = [
    ["поле", { status: "idle" }, "field", null, true, false],
    ["ожидание", { status: "pending", question: ASKED }, "waiting", "Готовим ответ…", true, false],
    [
      "ответ",
      { status: "success", question: ASKED, data: answer([CLAIM]) },
      "answer",
      null,
      true,
      false,
    ],
    [
      "пустой ответ",
      { status: "success", question: ASKED, data: answer([]) },
      "empty",
      "В базе знаний ответа нет",
      true,
      false,
    ],
    // 502, 503 и сбой сети мутация отдаёт одним статусом `error`; различие — в браузерном тесте.
    [
      "ошибка",
      { status: "error", question: ASKED },
      "error",
      "Справка недоступна, повторите позже",
      false,
      true,
    ],
    // openapi-fetch не считает ошибкой ответ без тела (502 прокси): ответа нет — справка недоступна.
    [
      "ответ без тела",
      { status: "success", question: ASKED },
      "error",
      "Справка недоступна, повторите позже",
      false,
      true,
    ],
  ];
  test.each(rows)("%s", (_, request, kind, notice, form, retry) => {
    const view = questionView(request);
    expect(view.kind).toBe(kind);
    expect(view.notice).toBe(notice);
    expect(view.form).toBe(form);
    expect(view.retry).toBe(retry);
  });

  test("before the first question only the intro is shown", () => {
    const view = questionView({ status: "idle" });
    expect(view.asked).toBeNull();
    expect(view.intro).toBe(true);
    expect(view.claims).toEqual([]);
  });

  test("waiting keeps the question on top and locks the field", () => {
    const view = questionView({ status: "pending", question: ASKED });
    expect(view.asked).toBe(ASKED);
    expect(view.locked).toBe(true);
    expect(view.intro).toBe(false);
  });

  test("answer shows its claims; the field is free for a new question", () => {
    const view = questionView({ status: "success", question: ASKED, data: answer([CLAIM]) });
    expect(view.claims).toEqual([CLAIM]);
    expect(view.locked).toBe(false);
  });

  test("a new question replaces the previous answer while it is prepared", () => {
    const next = questionView({ status: "pending", question: "Сколько продолжать лечение?" });
    expect(next.asked).toBe("Сколько продолжать лечение?");
    expect(next.claims).toEqual([]);
  });

  test("retry after an error asks the same question again", () => {
    const failed = questionView({ status: "error", question: ASKED });
    expect(failed.asked).toBe(ASKED);
    const again = questionView({ status: "pending", question: failed.asked ?? "" });
    expect(again.kind).toBe("waiting");
    expect(again.asked).toBe(ASKED);
  });
});

describe("questionBody: what goes to POST /knowledge/answers", () => {
  test("question is trimmed; no source_ids: the whole corpus is searched", () => {
    expect(questionBody("  Сколько продолжать лечение?\n")).toEqual({
      question: "Сколько продолжать лечение?",
    });
  });

  test.each([[""], ["   "], ["\n\t "]])("empty question %j is not sent", (draft) => {
    expect(questionBody(draft)).toBeNull();
  });

  test("the limit is 1000 characters, as on the server", () => {
    expect(QUESTION_MAX).toBe(1000);
    expect(questionBody("в".repeat(QUESTION_MAX))).not.toBeNull();
    expect(questionBody("в".repeat(QUESTION_MAX + 1))).toBeNull();
  });
});

describe("sendsOnKey: Enter sends, Shift+Enter breaks the line", () => {
  test.each([
    ["Enter", { key: "Enter", shiftKey: false, isComposing: false, keyCode: 13 }, true],
    ["Shift+Enter", { key: "Enter", shiftKey: true, isComposing: false, keyCode: 13 }, false],
    [
      "Enter при наборе IME",
      { key: "Enter", shiftKey: false, isComposing: true, keyCode: 229 },
      false,
    ],
    // Safari заканчивает набор IME нажатием Enter с isComposing: false и keyCode 229.
    [
      "Enter IME в Safari",
      { key: "Enter", shiftKey: false, isComposing: false, keyCode: 229 },
      false,
    ],
    ["другая клавиша", { key: "a", shiftKey: false, isComposing: false, keyCode: 65 }, false],
  ])("%s", (_, event, sends) => {
    expect(sendsOnKey(event)).toBe(sends);
  });
});

// Уровни УУР и УДД под источником считает levelsText (guidance.test.ts), здесь — строка источника.
describe("claimSource: document and section under the quote", () => {
  test("guideline with levels: «КР «…»» and section", () => {
    expect(claimSource(CLAIM)).toBe(
      "КР «Генерализованное тревожное расстройство», 3.1. Медикаментозное лечение",
    );
  });

  test("drug label: document and numbered section", () => {
    expect(claimSource({ ...CLAIM, fragment: LABEL })).toBe(
      "АВИАНДР (маритупирдин): общая характеристика лекарственного препарата, " +
        "4.2. Режим дозирования и способ применения",
    );
  });
});

test("answeredText: «Ответ» with day, month and time", () => {
  expect(answeredText(Date.parse("2026-10-19T06:52:00Z"), "Europe/Moscow")).toBe(
    "Ответ, 19.10, 09:52",
  );
});

test("texts: caption, hint and title of the inspector", () => {
  expect(QUESTION_TEXT.title).toBe("Вопрос к базе знаний");
  expect(QUESTION_TEXT.caption).toBe("Справка по базе знаний. Решение о терапии принимает врач.");
  expect(QUESTION_TEXT.hint).toBe("Без ФИО и других данных пациента");
});
