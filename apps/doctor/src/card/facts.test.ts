import { describe, expect, test } from "vitest";
import {
  decidedView,
  decisionErrorText,
  documentFacts,
  drugCheck,
  factsSummary,
  factView,
  knownView,
  nextPending,
  shortName,
  waitingLabel,
} from "./facts";
import { DOCTOR, document, proposal } from "./fixtures";

const NOW = new Date(2026, 9, 22, 12, 0);
const at = (hour: number, minute: number, day = 22) =>
  new Date(2026, 9, day, hour, minute).toISOString();
const ME = { id: DOCTOR, full_name: "Соколова Ирина Павловна" };
const base = { document_id: "d1", quote: "цитата", page: 1, ocr: false };

const diagnosis = (payload = {}) => ({
  ...base,
  icd10: "G47.0",
  text: "Нарушения засыпания и поддержания сна",
  code_in_text: false,
  ...payload,
});

const medication = (payload = {}) => ({
  ...base,
  name: "Мелатонин",
  name_in_text: "мелатонин",
  renamed: false,
  dose: "3 мг",
  regimen: "на ночь",
  status: "current",
  stopped_on: null,
  stopped_on_source: null,
  document_date: "2026-09-12",
  drug: { name: "Мелатонин", check: "registered", matches: [] },
  ...payload,
});

const fact = (field: string, text: string) => ({ ...base, field, fact: text });

describe("documentFacts", () => {
  test("groups pending facts of the document by kind in document order", () => {
    const list = [
      proposal({ id: "risk", kind: "ingestion.fact", payload: fact("risk_factor", "Курит") }),
      proposal({ id: "med", kind: "ingestion.medication", payload: medication() }),
      proposal({
        id: "late",
        kind: "ingestion.fact",
        payload: { ...fact("anamnesis", "СКФ"), page: 3 },
      }),
      proposal({ id: "early", kind: "ingestion.fact", payload: fact("anamnesis", "Тревога") }),
      proposal({ id: "dx", kind: "ingestion.diagnosis", payload: diagnosis() }),
      proposal({
        id: "other-doc",
        kind: "ingestion.diagnosis",
        payload: { ...diagnosis(), document_id: "d2" },
      }),
      proposal({ id: "finding", kind: "freetext.finding", payload: { document_id: "d1" } }),
    ];

    const facts = documentFacts(list, document({ id: "d1" }));

    expect(facts.groups.map((g) => [g.title, g.pending.map((p) => p.id)])).toEqual([
      ["Диагнозы", ["dx"]],
      ["Препараты", ["med"]],
      ["Анамнез", ["early", "late"]],
      ["Факторы риска", ["risk"]],
    ]);
    expect(facts.pending).toBe(5);
  });

  test("decided facts go to their own list in decision order, empty groups are hidden", () => {
    const decided = (id: string, decidedAt: string) =>
      proposal({
        id,
        kind: "ingestion.fact",
        payload: fact("anamnesis", id),
        status: "confirmed",
        decided_by: DOCTOR,
        decided_at: decidedAt,
      });

    const facts = documentFacts(
      [decided("second", at(10, 52)), decided("first", at(10, 51))],
      document({ id: "d1" }),
    );

    expect([facts.groups, facts.decided.map((p) => p.id), facts.pending]).toEqual([
      [],
      ["first", "second"],
      0,
    ]);
  });

  test("facts already in the card come from the document", () => {
    const known = [
      { kind: "ingestion.diagnosis" as const, payload: diagnosis({ icd10: "F41.1" }) },
    ];

    expect(documentFacts([], document({ known })).known).toEqual(known);
  });
});

describe("factView", () => {
  test("a diagnosis with a code from the model is marked so", () => {
    expect(factView("ingestion.diagnosis", diagnosis())).toEqual({
      title: "Нарушения засыпания и поддержания сна",
      subtitle: "G47.0 · код предложен моделью",
      subtitleIsQuote: false,
      marker: null,
      quote: "цитата",
      page: 1,
      effect: "Диагноз G47.0 появится в карточке",
      kindLabel: "диагноз",
    });
  });

  test("a diagnosis with the code written in the text shows only the code", () => {
    expect(factView("ingestion.diagnosis", diagnosis({ code_in_text: true })).subtitle).toBe(
      "G47.0",
    );
  });

  // Даты у препаратов — раздел 2 спеки #30, «Дата документа».
  test.each([
    [
      "current on the document date",
      {},
      "3 мг, на ночь · принимал на 12.09.2026",
      "Препарат появится в карточке как текущий, проверки терапии учтут его",
    ],
    [
      "current without a document date",
      { document_date: null, dose: null, regimen: null },
      "принимал на дату документа",
      "Препарат появится в карточке как текущий, проверки терапии учтут его",
    ],
    [
      "stopped on a date, renamed to the registry name",
      {
        name: "Афобазол",
        name_in_text: "афобазола",
        renamed: true,
        dose: "10 мг",
        regimen: "3 раза в день",
        status: "past",
        stopped_on: "2026-08-30",
      },
      "10 мг, 3 раза в день · отменён 30.08.2026 · в тексте: «афобазола»",
      "Препарат появится в карточке как отменённый 30.08.2026, проверки терапии учтут отмену",
    ],
    [
      "stopped on a date computed from the document date",
      { status: "past", stopped_on: "2026-09-02", stopped_on_source: "computed" },
      "3 мг, на ночь · отменён 02.09.2026, дата вычислена от даты документа",
      "Препарат появится в карточке как отменённый 02.09.2026, проверки терапии учтут отмену",
    ],
    [
      "stopped on a date written in the text",
      { status: "past", stopped_on: "2026-09-02", stopped_on_source: "text" },
      "3 мг, на ночь · отменён 02.09.2026",
      "Препарат появится в карточке как отменённый 02.09.2026, проверки терапии учтут отмену",
    ],
    [
      "stopped without a date",
      { status: "past", dose: null },
      "на ночь · отменён, дата не указана",
      "Сохранится как прошлый приём без даты отмены. В список препаратов не попадёт",
    ],
  ])("a medication %s", (_, payload, subtitle, effect) => {
    const view = factView("ingestion.medication", medication(payload));

    expect([view.subtitle, view.effect, view.kindLabel]).toEqual([subtitle, effect, "препарат"]);
  });

  test("a medication carries its registry check", () => {
    const view = factView(
      "ingestion.medication",
      medication({ drug: { name: "Магний хелат", check: "not_found", matches: [] } }),
    );

    expect(view.marker).toEqual({ tone: "warning", text: "Нет в ГРЛС: проверьте название" });
  });

  test.each([
    ["anamnesis", null, "анамнез", "Факт сохранится в анамнезе пациента"],
    [
      "risk_factor",
      { tone: "risk", text: "Фактор риска" },
      "фактор риска",
      "Фактор риска сохранится в карточке пациента",
    ],
  ])("a %s fact is quoted under its text", (field, marker, kindLabel, effect) => {
    const view = factView("ingestion.fact", fact(field, "Работа посменно"));

    expect(view).toEqual({
      title: "Работа посменно",
      subtitle: "«цитата»",
      subtitleIsQuote: true,
      marker,
      quote: "цитата",
      page: 1,
      effect,
      kindLabel,
    });
  });
});

describe("drugCheck", () => {
  // Статусы сверки — спека #32, 3.3; слова — кадры 04 и 07в.
  test.each([
    ["registered", [], { tone: "ok", text: "Есть в ГРЛС" }],
    ["not_found", [], { tone: "warning", text: "Нет в ГРЛС: проверьте название" }],
    ["inactive", [], { tone: "warning", text: "Регистрация в ГРЛС не действует" }],
    ["suspended", [], { tone: "warning", text: "Применение приостановлено в ГРЛС" }],
    ["unknown", [], { tone: "warning", text: "Статус в ГРЛС не определён" }],
    [
      "similar",
      [
        { trade_name: "Афобазол", inn: "Фабомотизол", reg_number: "ЛП-1" },
        { trade_name: null, inn: "Мелатонин", reg_number: null },
      ],
      { tone: "warning", text: "Возможно, в ГРЛС: Афобазол (Фабомотизол), Мелатонин" },
    ],
  ])("%s", (check, matches, view) => {
    expect(drugCheck({ name: "X", check, matches })).toEqual(view);
  });
});

test.each([
  [
    { kind: "ingestion.diagnosis" as const, payload: diagnosis({ icd10: "F41.1", text: "ГТР" }) },
    { title: "F41.1 ГТР", subtitle: "Диагноз уже есть в карточке, предложения нет", page: 1 },
  ],
  [
    {
      kind: "ingestion.medication" as const,
      payload: { ...medication({ page: null }), status: "current" as const },
    },
    { title: "Мелатонин", subtitle: "Препарат уже есть в карточке, предложения нет", page: null },
  ],
])("knownView %#", (known, view) => {
  expect(knownView(known)).toEqual(view);
});

describe("decidedView", () => {
  const decided = (overrides = {}) =>
    proposal({
      kind: "ingestion.fact",
      payload: fact("anamnesis", "Трудно засыпает"),
      status: "confirmed",
      decided_by: DOCTOR,
      decided_at: at(10, 51),
      ...overrides,
    });

  test.each([
    ["confirmed by me", {}, "Подтверждено врачом, Соколова И. П. · анамнез", "10:51"],
    [
      "rejected by me with a reason",
      { status: "rejected", decision_comment: "бросила в 2025 г.", decided_at: at(9, 0, 20) },
      "Отклонено: бросила в 2025 г. · Соколова И. П.",
      "20.10",
    ],
    [
      "rejected by another doctor",
      { status: "rejected", decided_by: "other" },
      "Отклонено другим врачом · анамнез",
      "10:51",
    ],
  ])("%s", (_, overrides, subtitle, time) => {
    const view = decidedView(decided(overrides), ME, NOW);

    expect([view.title, view.subtitle, view.time]).toEqual(["Трудно засыпает", subtitle, time]);
  });
});

test.each([
  [1, "ждёт решения: 1"],
  [3, "ждут решения: 3"],
  [21, "ждёт решения: 21"],
])("waitingLabel %i", (n, label) => {
  expect(waitingLabel(n)).toBe(label);
});

describe("factsSummary", () => {
  const doc = document({ extracted_at: at(10, 46) });

  test.each([
    [{ pending: 8, decided: 0, known: 0 }, "Разобран сегодня, 10:46 · 8 фактов ждут решения"],
    [{ pending: 1, decided: 2, known: 0 }, "Разобран сегодня, 10:46 · 1 факт ждёт решения"],
    [{ pending: 0, decided: 2, known: 1 }, "Разобран сегодня, 10:46 · все факты рассмотрены"],
    [{ pending: 0, decided: 0, known: 0 }, "Разобран сегодня, 10:46 · фактов не найдено"],
  ])("%j", (counts, text) => {
    expect(factsSummary(doc, counts, NOW)).toBe(text);
  });
});

describe("decisionErrorText", () => {
  test.each([
    [{ status: 409, detail: "Proposal is already decided" }, "предложение уже рассмотрено", true],
    [
      { status: 409, detail: "Proposal kind cannot be applied" },
      "система не умеет применять предложения этого вида, решение не сохранено",
      false,
    ],
    // Повтор диагноза или препарата: applier отвечает 422 по-русски (спека #30, 3.5).
    [
      { status: 422, detail: "Диагноз с этим кодом уже есть в карточке" },
      "диагноз с этим кодом уже есть в карточке",
      false,
    ],
    [
      { status: 422, detail: [{ loc: ["body"] }] },
      "решение не сохранено, проверьте связь и попробуйте ещё раз",
      false,
    ],
    [
      new TypeError("Failed to fetch"),
      "решение не сохранено, проверьте связь и попробуйте ещё раз",
      false,
    ],
  ])("%j", (error, text, stale) => {
    expect(decisionErrorText(error)).toEqual({ text, stale });
  });
});

test.each([
  ["Соколова Ирина Павловна", "Соколова И. П."],
  ["Соколова Ирина", "Соколова И."],
  ["Соколова", "Соколова"],
])("shortName %s", (full, short) => {
  expect(shortName(full)).toBe(short);
});

test.each([
  ["a", "b"],
  ["c", null],
  ["missing", null],
])("after deciding %s the next fact is %s", (decided, next) => {
  const group = ["a", "b", "c"].map((id) => proposal({ id }));

  expect(nextPending(group, decided)).toBe(next);
});
