import type { Schemas } from "@compass/api-client";
import { describe, expect, test } from "vitest";
import { blockState, documentTitle, registryText, sourceBlock, versionText } from "./sourceView";

/** Выдуманный ответ ручки источника. Тексты — заглушки, не цитаты документов. */
type SourceView = Schemas["SourceView"];
type Fragment = SourceView["sections"][number]["fragments"][number];

const OHLP = {
  id: "aviandr-ohlp",
  title: "АВИАНДР: общая характеристика лекарственного препарата",
  kind: "drug_label",
  publisher: "ООО «Авинейро»",
  version: "0010",
  date: "2026-10-14",
  url: "https://eaeu.example/register",
  supplementary: false,
};

function fragment(overrides: Partial<Fragment> = {}): Fragment {
  return {
    id: "f1",
    key: "k1",
    document: {
      id: "d1",
      source_id: "aviandr-ohlp",
      title: OHLP.title,
      version: "0010",
      url: OHLP.url,
      kind: "drug_label",
      status: "active",
    },
    section: "4.3",
    section_title: "Противопоказания",
    heading_path: "4. Клинические данные > 4.3 Противопоказания",
    kind: "text",
    text: "Текст раздела.",
    ...overrides,
  } as Fragment;
}

function view(overrides: Partial<SourceView> = {}): SourceView {
  return {
    source: OHLP,
    registry: { reg_number: "ЛП-№(002445)-(РГ-RU)", status: "active", snapshot_date: "2026-10-16" },
    locator: "4.3",
    found: true,
    sections: [{ number: "4.3", title: "Противопоказания", fragments: [fragment()] }],
    ...overrides,
  } as SourceView;
}

const REF = { source: "aviandr-ohlp", title: "ОХЛП", locator: "4.3" };

describe("sourceBlock", () => {
  test("label: document, section, version with date, publisher, registry and registry link", () => {
    const block = sourceBlock(view(), REF);

    expect(block.title).toBe(OHLP.title);
    expect(block.fields).toEqual([
      { label: "Раздел", value: "4.3" },
      { label: "Версия", value: "0010 от 14.10.2026" },
      { label: "Издатель", value: "ООО «Авинейро»" },
      { label: "Номер РУ", value: "ЛП-№(002445)-(РГ-RU), действует по ГРЛС на 16.10.2026" },
    ]);
    expect(block.link).toEqual({ label: "Открыть в реестре", url: OHLP.url });
    expect(block.supplementary).toBe(false);
    expect(block.found).toBe(true);
  });

  test("guideline without registry links to the document", () => {
    const block = sourceBlock(
      view({
        source: {
          ...OHLP,
          kind: "clinical_guideline",
          title: "Депрессивный эпизод",
          supplementary: true,
        },
        registry: null,
      }),
      REF,
    );

    expect(block.title).toBe("Клинические рекомендации «Депрессивный эпизод»");
    expect(block.fields.map((f) => f.label)).toEqual(["Раздел", "Версия", "Издатель"]);
    expect(block.link).toEqual({ label: "Открыть документ", url: OHLP.url });
    expect(block.supplementary).toBe(true);
  });

  test("section not found keeps the document and the locator", () => {
    const block = sourceBlock(view({ found: false, sections: [], locator: "прил. Г1" }), {
      ...REF,
      locator: "прил. Г1",
    });

    expect(block.found).toBe(false);
    expect(block.fields[0]).toEqual({ label: "Раздел", value: "прил. Г1" });
    expect(block.fragments).toEqual([]);
  });

  test("fragment reference names its section and highlights the quote", () => {
    const block = sourceBlock(
      view({
        locator: null,
        sections: [
          {
            number: null,
            title: "3.1 Медикаментозное лечение",
            fragments: [fragment({ text: "До цитаты. Цитата утверждения. После." })],
          },
        ],
      }),
      {
        source: "aviandr-ohlp",
        title: "ОХЛП",
        locator: null,
        fragmentId: "f1",
        quote: "Цитата утверждения.",
      },
    );

    expect(block.fields[0]).toEqual({ label: "Раздел", value: "3.1 Медикаментозное лечение" });
    expect(block.fragments[0]?.parts).toEqual({
      before: "До цитаты. ",
      quote: "Цитата утверждения.",
      after: " После.",
    });
  });

  test("fragments in order; a heading is shown when it changes", () => {
    const block = sourceBlock(
      view({
        locator: "4.3; 4.2",
        sections: [
          {
            number: "4.3",
            title: "Противопоказания",
            fragments: [fragment({ id: "a" }), fragment({ id: "b" })],
          },
          {
            number: "4.2",
            title: "Режим дозирования",
            fragments: [fragment({ id: "c", heading_path: "4. Клинические данные > 4.2 Режим" })],
          },
        ],
      }),
      REF,
    );

    expect(block.fragments.map((f) => [f.id, f.heading])).toEqual([
      ["a", "4. Клинические данные > 4.3 Противопоказания"],
      ["b", null],
      ["c", "4. Клинические данные > 4.2 Режим"],
    ]);
    expect(block.fragments[0]?.parts).toBeNull();
  });

  test("text of another version and of a superseded document is marked", () => {
    const old = fragment().document;
    const block = sourceBlock(
      view({
        sections: [
          {
            number: "4.3",
            title: "Противопоказания",
            fragments: [
              fragment({ id: "a", document: { ...old, version: "0009" } }),
              fragment({ id: "b", document: { ...old, version: "0009" } }),
              fragment({ id: "c", document: { ...old, version: "0008", status: "superseded" } }),
              fragment({ id: "d" }),
            ],
          },
        ],
      }),
      REF,
    );

    expect(block.fragments.map((f) => f.notes)).toEqual([
      ["Текст — версия 0009"],
      [],
      ["Прежняя версия документа", "Текст — версия 0008"],
      [],
    ]);
  });
});

describe("blockState", () => {
  const failed = { status: 502, title: "Bad Gateway" };
  // Источника нет в манифесте или фрагмента нет: повтор не поможет.
  const missing = { status: 404, title: "Not Found", detail: "Source not found" };

  test.each([
    [{ isError: false, isFetching: true, data: undefined, error: null }, "loading"],
    [{ isError: true, isFetching: false, data: undefined, error: failed }, "error"],
    [{ isError: true, isFetching: false, data: undefined, error: missing }, "missing"],
    // Повтор после ошибки: пока запрос идёт, снова загрузка.
    [{ isError: true, isFetching: true, data: undefined, error: failed }, "loading"],
    [{ isError: false, isFetching: false, data: view(), error: null }, "found"],
    [{ isError: false, isFetching: false, data: view({ found: false }), error: null }, "not-found"],
  ] as const)("%o is %s", (query, state) => {
    expect(blockState(query)).toBe(state);
  });
});

describe("texts", () => {
  test.each([
    [
      { reg_number: "ЛП-1", status: "active", snapshot_date: "2026-10-16" },
      "ЛП-1, действует по ГРЛС на 16.10.2026",
    ],
    [
      { reg_number: "ЛП-1", status: "inactive", snapshot_date: "2026-10-16" },
      "ЛП-1, не действует по ГРЛС на 16.10.2026",
    ],
    [
      { reg_number: "ЛП-1", status: "suspended", snapshot_date: "2026-10-16" },
      "ЛП-1, приостановлено по ГРЛС на 16.10.2026",
    ],
    [
      { reg_number: "ЛП-1", status: "unknown", snapshot_date: "2026-10-16" },
      "ЛП-1, статус в ГРЛС на 16.10.2026 не указан",
    ],
  ] as const)("registry %o", (registry, text) => {
    expect(registryText(registry)).toBe(text);
  });

  test("version with the date of the manifest", () => {
    expect(versionText("301_3", "2024-08-26")).toBe("301_3 от 26.08.2024");
  });

  test("only guidelines get the document type in the title", () => {
    expect(documentTitle({ kind: "scale_validation", title: "PHQ-9: валидация" })).toBe(
      "PHQ-9: валидация",
    );
  });
});
