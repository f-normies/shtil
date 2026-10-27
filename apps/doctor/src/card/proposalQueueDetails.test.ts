import { describe, expect, test } from "vitest";
import { queueItem as item } from "./fixtures";
import {
  applicability,
  findingSource,
  outcomeText,
  quoteCaption,
  rowView,
  therapyBasis,
} from "./proposalQueue";

/** Подробности предложения в очереди (спека #96, 3.5): строка, источник, применимость. */

describe("applicability", () => {
  const drug = (check: string, matches: unknown[] = []) => ({
    name: "Суматриптан",
    check,
    matches,
  });

  test("a finding names the drug and its registry check in words", () => {
    expect(applicability(item({ payload: { drug: drug("registered") } }))).toEqual([
      { icon: "drug", text: "Суматриптан", detail: "Найден в ГРЛС", warning: false },
    ]);
  });

  test.each([
    ["not_found", "Нет в ГРЛС"],
    ["inactive", "Регистрация не действует"],
    ["suspended", "Применение приостановлено"],
    ["unknown", "Статус в ГРЛС не определён"],
  ])("a drug check %s is highlighted", (check, detail) => {
    expect(applicability(item({ payload: { drug: drug(check) } }))).toEqual([
      { icon: "drug", text: "Суматриптан", detail, warning: true },
    ]);
  });

  test("a similar name offers registered names", () => {
    const similar = drug("similar", [{ trade_name: "Сумамигрен", inn: "Суматриптан" }]);

    expect(applicability(item({ payload: { drug: similar } }))[0]).toMatchObject({
      detail: "Возможно: Сумамигрен (Суматриптан)",
      warning: true,
    });
  });

  test("an adherence event is a fact of the finding", () => {
    expect(
      applicability(item({ payload: { category: "adherence", adherence_event: "missed" } })),
    ).toEqual([
      { icon: "calendar", text: "Приём препарата: пропустил", detail: null, warning: false },
    ]);
  });

  test("a prescription shows the indication and the patient profile from its checks", () => {
    const checks = [
      { id: "registered", title: "Зарегистрирован в РФ", status: "pass", detail: "ЛП-1" },
      { id: "indication", title: "Показание: ГТР", status: "pass", detail: "диагноз F41.1" },
      { id: "age", title: "Возраст от 18 лет", status: "pass", detail: "34 года" },
    ];
    const rows = applicability(
      item({
        kind: "therapy.prescription",
        payload: { indication_title: "Генерализованное тревожное расстройство", checks },
      }),
    );

    expect(rows).toEqual([
      {
        icon: "indication",
        text: "Показание: Генерализованное тревожное расстройство",
        detail: "Диагноз F41.1",
        warning: false,
      },
      { icon: "profile", text: "Возраст от 18 лет", detail: "34 года", warning: false },
    ]);
  });

  test("a check that is not passed is highlighted", () => {
    const checks = [
      {
        id: "age",
        title: "Возраст от 18 лет",
        status: "manual",
        detail: "дата рождения не указана",
      },
    ];

    expect(
      applicability(item({ kind: "therapy.prescription", payload: { checks } }))[0],
    ).toMatchObject({ detail: "Дата рождения не указана", warning: true });
  });

  test("a case history fact names the document page", () => {
    expect(
      applicability(
        item({ kind: "ingestion.fact", payload: { document_id: "d1", page: 2, ocr: true } }),
      ),
    ).toEqual([
      {
        icon: "document",
        text: "История болезни, стр. 2",
        detail: "Текст распознан с изображения: названия могли исказиться",
        warning: true,
      },
    ]);
  });

  test("a medication from a case history also shows the document date", () => {
    const rows = applicability(
      item({
        kind: "ingestion.medication",
        payload: { document_id: "d1", page: null, ocr: false, document_date: "2026-09-12" },
      }),
    );

    expect(rows).toEqual([
      {
        icon: "document",
        text: "История болезни",
        detail: "Документ от 12.09.2026",
        warning: false,
      },
    ]);
  });

  test("a referral has no applicability rows", () => {
    expect(applicability(item({ kind: "alerts.referral", payload: { title: "т" } }))).toEqual([]);
  });
});

describe("findingSource", () => {
  const run = {
    model: "qwen3.6",
    prompt_version: "extract-7a3603ce",
    created_at: new Date(2026, 9, 19, 8, 31).toISOString(),
  };

  test("a voice record in the diary with its time and the analysis rules version", () => {
    const at = new Date(2026, 9, 19, 8, 30).toISOString();
    const source = findingSource(
      item({ run, payload: { source_type: "diary", transcript: true, recorded_at: at } }),
    );

    expect(source).toEqual({
      record: "Голосовая запись в дневнике",
      note: "Цитата дословно из расшифровки. Названия в ней могут быть распознаны с ошибками",
      voice: true,
      at,
      rules: "extract-7a3603ce, 19.10.2026",
    });
  });

  test.each([
    ["diary", false, "Запись в дневнике"],
    ["message", false, "Сообщение в чате"],
    ["message", true, "Голосовое сообщение в чате"],
  ])("record type %s (voice: %s) is «%s»", (sourceType, transcript, record) => {
    const source = findingSource(item({ payload: { source_type: sourceType, transcript } }));

    expect(source.record).toBe(record);
  });

  test("an old finding without the record time shows the proposal time", () => {
    const source = findingSource(item({ created_at: "2026-10-18T08:13:00Z", payload: {} }));

    expect(source.at).toBe("2026-10-18T08:13:00Z");
  });

  test("without an analysis row there is no rules version", () => {
    expect(findingSource(item({ run: null })).rules).toBeNull();
    expect(findingSource(item({ run: undefined })).rules).toBeNull();
  });

  test("a run without a prompt version says so", () => {
    expect(findingSource(item({ run: { ...run, prompt_version: null } })).rules).toBe(
      "версия не записана, 19.10.2026",
    );
  });
});

describe("rowView", () => {
  test("a finding: category, short summary and the record", () => {
    const row = rowView(
      item({
        payload: {
          category: "side_effect",
          summary: "сведение челюсти, потливость",
          source_type: "diary",
          transcript: true,
        },
      }),
    );

    expect(row).toEqual({
      title: "Побочный эффект",
      detail: "Сведение челюсти, потливость",
      meta: "Голосовая запись в дневнике",
    });
  });

  test("a drug finding adds the registry check", () => {
    const row = rowView(
      item({
        payload: {
          category: "medication",
          summary: "суматриптан от мигрени",
          drug: { name: "Суматриптан", check: "registered", matches: [] },
          source_type: "message",
          transcript: false,
        },
      }),
    );

    expect(row.detail).toBe("Суматриптан от мигрени · найден в ГРЛС");
  });

  test("a case history fact shows the page", () => {
    const row = rowView(
      item({
        kind: "ingestion.fact",
        payload: {
          document_id: "d1",
          field: "anamnesis",
          fact: "Трудно засыпает",
          quote: "q",
          page: 3,
        },
      }),
    );

    expect(row).toEqual({
      title: "Анамнез из истории болезни",
      detail: "Трудно засыпает",
      meta: "История болезни, стр. 3",
    });
  });
});

describe("therapyBasis", () => {
  const indication = { source: "label-aviandr", locator: "4.1", title: "ОХЛП Авиандр", url: "u" };
  const regimen = { source: "label-aviandr", locator: "4.2", title: "ОХЛП Авиандр", url: "u" };

  test("a prescription rests on the label sections of the indication and the regimen", () => {
    const basis = therapyBasis(
      item({
        kind: "therapy.prescription",
        payload: {
          trade_name: "Авиандр",
          indication_title: "ГТР",
          regimen: { source: regimen },
          checks: [
            {
              id: "indication",
              title: "Показание: ГТР",
              status: "pass",
              detail: "диагноз F41.1",
              source: indication,
            },
          ],
        },
      }),
    );

    expect(basis).toEqual({
      heading: "Назначение «Авиандр» по показанию «ГТР»",
      applicability: ["Показание: ГТР — диагноз F41.1"],
      refs: [
        { source: "label-aviandr", locator: "4.1", title: "ОХЛП Авиандр", url: "u" },
        { source: "label-aviandr", locator: "4.2", title: "ОХЛП Авиандр", url: "u" },
      ],
    });
  });

  test("other proposals have no therapy basis", () => {
    expect(therapyBasis(item({ kind: "therapy.stop", payload: { reason: "r" } }))).toBeNull();
    expect(therapyBasis(item())).toBeNull();
  });
});

describe("quoteCaption", () => {
  test("a finding quote is signed with the record", () => {
    const finding = item({ payload: { source_type: "diary", transcript: true } });

    expect(quoteCaption(finding)).toBe("Цитата пациента, голосовая запись в дневнике");
  });

  test("a case history quote is signed with the page", () => {
    expect(quoteCaption(item({ kind: "ingestion.fact", payload: { page: 2 } }))).toBe(
      "Цитата из истории болезни, стр. 2",
    );
    expect(quoteCaption(item({ kind: "ingestion.fact", payload: {} }))).toBe(
      "Цитата из истории болезни",
    );
  });
});

describe("outcomeText", () => {
  const at = new Date(2026, 9, 19, 8, 30).toISOString();
  const format = () => "19.10.2026, 08:30";

  test("confirmed by me", () => {
    const decided = item({ status: "confirmed", decided_by: "me", decided_at: at });

    expect(outcomeText(decided, "me", format)).toEqual({
      text: "Подтверждено вами, 19.10.2026, 08:30",
      reason: null,
    });
  });

  test("rejected by another doctor with a reason", () => {
    const decided = item({
      status: "rejected",
      decided_by: "other",
      decided_at: at,
      decision_comment: " приняла позже, уточнила в чате ",
    });

    expect(outcomeText(decided, "me", format)).toEqual({
      text: "Отклонено другим врачом, 19.10.2026, 08:30",
      reason: "Причина: приняла позже, уточнила в чате",
    });
  });

  test("rejected without a reason says so", () => {
    const decided = item({ status: "rejected", decided_by: "me", decided_at: null });

    expect(outcomeText(decided, "me", format)).toEqual({
      text: "Отклонено вами",
      reason: "Причина не указана",
    });
  });
});
