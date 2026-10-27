import { describe, expect, test } from "vitest";
import { proposal } from "./fixtures";
import { caseHistoryDocument, isTherapy, pendingProposals, proposalView } from "./proposals";

describe("pendingProposals", () => {
  test("keeps proposals waiting for a decision, newest first", () => {
    const list = [
      proposal({ id: "old", created_at: "2026-10-17T08:00:00Z" }),
      proposal({ id: "done", status: "confirmed" }),
      proposal({ id: "new", created_at: "2026-10-18T08:00:00Z" }),
    ];

    expect(pendingProposals(list).map((p) => p.id)).toEqual(["new", "old"]);
  });
});

describe("proposalView", () => {
  test("describes a freetext finding with quote, record and adherence event", () => {
    const view = proposalView(
      proposal({
        payload: {
          source_type: "diary",
          transcript: true,
          category: "adherence",
          summary: "пропустил приём",
          quote: "забыл выпить таблетку",
          drug: { name: "Авиандр", check: "registered", matches: [] },
          adherence_event: "missed",
        },
      }),
    );

    expect(view).toEqual({
      title: "Приём препарата",
      summary: "пропустил приём",
      quote: "забыл выпить таблетку",
      recordLabel: "голосовая запись",
      adherence: "пропустил",
      drug: { name: "Авиандр", warning: null },
      fields: [],
    });
  });

  test.each([
    ["registered", null],
    ["not_found", "нет в ГРЛС"],
    ["inactive", "регистрация не действует"],
    ["suspended", "применение приостановлено"],
    ["unknown", "статус в ГРЛС не определён"],
  ])("warns about drug check %s", (check, warning) => {
    const view = proposalView(
      proposal({ payload: { category: "medication", drug: { name: "X", check, matches: [] } } }),
    );

    expect(view.drug).toEqual({ name: "X", warning });
  });

  test("offers similar registered names", () => {
    const view = proposalView(
      proposal({
        payload: {
          category: "medication",
          drug: {
            name: "авиандор",
            check: "similar",
            matches: [{ trade_name: "АВИАНДР®", inn: "Маритупирдин", reg_number: "ЛП-1" }],
          },
        },
      }),
    );

    expect(view.drug?.warning).toBe("возможно: АВИАНДР® (Маритупирдин)");
  });

  test("lists payload fields of other proposal kinds", () => {
    const view = proposalView(proposal({ kind: "therapy.prescribe", payload: { dose: "10 мг" } }));

    expect(view.title).toBe("therapy.prescribe");
    expect(view.fields).toEqual([{ name: "dose", value: "10 мг" }]);
  });
});

describe("therapy proposals", () => {
  const regimen = {
    form: "таблетки, 20 мг",
    dose_mg: 20,
    times_per_day: 2,
    schedule: "утром и вечером",
    duration_days: null,
    max_days: null,
    duration_text: "определяет врач",
    source: { source: "label", locator: "4.2", title: "ОХЛП", url: "https://example.org" },
  };

  test("a prescription names the drug, indication and regimen", () => {
    const view = proposalView(
      proposal({
        kind: "therapy.prescription",
        payload: {
          trade_name: "Авиандр",
          indication_title: "Генерализованное тревожное расстройство",
          regimen,
          off_label: false,
        },
      }),
    );

    expect([view.title, view.summary, view.fields]).toEqual([
      "Назначение: Авиандр",
      "Генерализованное тревожное расстройство: по 20 мг 2 раза в сутки, утром и вечером; длительность: определяет врач",
      [],
    ]);
  });

  test("an off-label prescription says so", () => {
    const view = proposalView(
      proposal({
        kind: "therapy.prescription",
        payload: { trade_name: "Авиандр", indication_title: "ГТР", regimen, off_label: true },
      }),
    );

    expect(view.title).toBe("Назначение вне инструкции: Авиандр");
  });

  test("a stop shows its reason", () => {
    const view = proposalView(
      proposal({ kind: "therapy.stop", payload: { reason: "скованность мышц" } }),
    );

    expect([view.title, view.summary]).toEqual(["Отмена назначения", "скованность мышц"]);
  });
});

test("therapy proposals are decided on the therapy page", () => {
  expect(isTherapy(proposal({ kind: "therapy.prescription" }))).toBe(true);
  expect(isTherapy(proposal({ kind: "therapy.stop" }))).toBe(true);
  expect(isTherapy(proposal({ kind: "freetext.finding" }))).toBe(false);
});

describe("case history proposals", () => {
  const base = { document_id: "d1", quote: "засыпает с трудом", page: 1, ocr: false };

  test.each([
    [
      "ingestion.diagnosis",
      { ...base, icd10: "G47.0", text: "Нарушения сна", code_in_text: true },
      "Диагноз из истории болезни",
      "Нарушения сна — G47.0",
    ],
    [
      "ingestion.medication",
      {
        ...base,
        name: "Мелатонин",
        name_in_text: "мелатонин",
        renamed: false,
        dose: "3 мг",
        regimen: null,
        status: "current",
        stopped_on: null,
        document_date: null,
        drug: { name: "Мелатонин", check: "registered", matches: [] },
      },
      "Препарат из истории болезни",
      "Мелатонин — 3 мг · принимал на дату документа",
    ],
    [
      "ingestion.fact",
      { ...base, field: "anamnesis", fact: "Трудно засыпает" },
      "Анамнез из истории болезни",
      "Трудно засыпает",
    ],
    [
      "ingestion.fact",
      { ...base, field: "risk_factor", fact: "Работа посменно" },
      "Фактор риска из истории болезни",
      "Работа посменно",
    ],
  ])("%s gets a short title, summary and quote", (kind, payload, title, summary) => {
    const view = proposalView(proposal({ kind, payload }));

    expect([view.title, view.summary, view.quote, view.fields]).toEqual([
      title,
      summary,
      "засыпает с трудом",
      [],
    ]);
  });

  test("a medication from a case history shows its registry check", () => {
    const view = proposalView(
      proposal({
        kind: "ingestion.medication",
        payload: {
          ...base,
          name: "Магний хелат",
          status: "current",
          drug: { name: "Магний хелат", check: "not_found", matches: [] },
        },
      }),
    );

    expect(view.drug).toEqual({ name: "Магний хелат", warning: "Нет в ГРЛС: проверьте название" });
  });

  test("a registered medication is not repeated under the summary", () => {
    const view = proposalView(
      proposal({
        kind: "ingestion.medication",
        payload: {
          ...base,
          name: "Мелатонин",
          status: "current",
          drug: { name: "Мелатонин", check: "registered", matches: [] },
        },
      }),
    );

    expect(view.drug).toBeNull();
  });

  test("case history proposals point to their document", () => {
    expect(caseHistoryDocument(proposal({ kind: "ingestion.fact", payload: base }))).toBe("d1");
    expect(caseHistoryDocument(proposal({ kind: "freetext.finding", payload: base }))).toBeNull();
  });
});
