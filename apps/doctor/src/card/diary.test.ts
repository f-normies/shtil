/**
 * Записи и находки вкладки «Дневник» (спека #115, разделы 3.5 и 3.6; кадры 06 и 06д)
 * на формах ручек #31: `DiaryDoctorEntryOut` и `DiaryAudioOut`.
 */
import { describe, expect, test } from "vitest";
import {
  audioUrl,
  canOpenFinding,
  dayTitle,
  diaryFailure,
  diaryFindings,
  entryRows,
  findingEntries,
  findingLabel,
  findingsByEntry,
  findingView,
  formatDuration,
  groupByDay,
  isLongText,
  locateEntry,
  matchesFilter,
  ownText,
  transcriptView,
} from "./diary";
import { audio, entry, finding, source, TODAY } from "./diaryFixtures";

describe("entries by day", () => {
  const entries = [
    entry({ entry_id: "text-17", occurred_at: "2026-10-17T20:40:00+03:00" }),
    entry({ entry_id: "voice-19", kind: "voice", occurred_at: "2026-10-19T09:31:00+03:00" }),
    entry({ entry_id: "daily-17", kind: "daily", occurred_at: "2026-10-17T09:12:00+03:00" }),
    entry({ entry_id: "daily-19", kind: "daily", occurred_at: "2026-10-19T09:10:00+03:00" }),
    entry({ entry_id: "mood-18", kind: "mood", occurred_at: "2026-10-18T16:02:00+03:00" }),
  ];

  test("days and entries go newest first", () => {
    const groups = groupByDay(entries, TODAY, "all");
    expect(groups.map((g) => g.title)).toEqual([
      "Сегодня, 19 октября",
      "Вчера, 18 октября",
      "Суббота, 17 октября",
    ]);
    expect(groups[0]?.entries.map((e) => e.entry_id)).toEqual(["voice-19", "daily-19"]);
    expect(groups[2]?.entries.map((e) => e.entry_id)).toEqual(["text-17", "daily-17"]);
  });

  test("the filter hides other kinds, the days stay", () => {
    const groups = groupByDay(
      entries.map((e) => (e.kind === "voice" ? { ...e, audio: audio() } : e)),
      TODAY,
      "voice",
    );
    expect(groups.map((g) => g.entries.map((e) => e.entry_id))).toEqual([["voice-19"], [], []]);
  });

  test.each([
    ["a voice record", "voice", true, { kind: "voice", audio: audio() }],
    ["a check-in told by voice", "voice", true, { kind: "daily", audio: audio(), mood_level: 3 }],
    ["a text record", "voice", false, { kind: "text" }],
    ["a text record", "text", true, { kind: "text" }],
    ["a check-in with a story in text", "text", false, { kind: "daily", mood_level: 3 }],
    ["a quick mood mark", "mood", true, { kind: "mood", mood_level: 2 }],
    ["a check-in", "mood", true, { kind: "daily", mood_level: 3 }],
    ["a voice record with a mood", "mood", true, { kind: "voice", audio: audio(), mood_level: 4 }],
    ["a text record", "mood", false, { kind: "text" }],
    ["a text record", "all", true, { kind: "text" }],
  ] as const)("%s, filter «%s»: %s", (_name, filter, matches, fields) => {
    expect(matchesFilter(entry(fields), filter)).toBe(matches);
  });

  test("while more pages may hold the last day, it is not called empty for the filter", () => {
    const loaded = [
      entry({
        entry_id: "voice-19",
        kind: "voice",
        audio: audio(),
        occurred_at: "2026-10-19T09:31:00+03:00",
      }),
      entry({ entry_id: "text-18", occurred_at: "2026-10-18T20:40:00+03:00" }),
    ];
    expect(groupByDay(loaded, TODAY, "voice", true).map((g) => g.title)).toEqual([
      "Сегодня, 19 октября",
    ]);
    expect(groupByDay(loaded, TODAY, "voice", false).map((g) => g.entries.length)).toEqual([1, 0]);
    expect(groupByDay(loaded, TODAY, "text", true).map((g) => g.entries.length)).toEqual([0, 1]);
  });

  test("a day of another year carries the year", () => {
    expect(dayTitle("2025-12-31", TODAY)).toBe("Среда, 31 декабря 2025");
  });
});

describe("entry contents", () => {
  test.each([
    [84_000, "1:24"],
    [42_000, "0:42"],
    [600_000, "10:00"],
    [1_499, "0:01"],
  ])("%i ms reads %s", (ms, text) => {
    expect(formatDuration(ms)).toBe(text);
  });

  test.each([
    ["pending", null, { status: "processing", text: "Расшифровывается…", spoken: false }],
    ["processing", null, { status: "processing", text: "Расшифровывается…", spoken: false }],
    [
      "done",
      "Ночью опять просыпалась.",
      { status: "done", text: "Ночью опять просыпалась.", spoken: true },
    ],
    [
      "done",
      " ",
      { status: "failed", text: "Не удалось разобрать, послушайте запись", spoken: false },
    ],
    [
      "failed",
      null,
      { status: "failed", text: "Не удалось разобрать, послушайте запись", spoken: false },
    ],
  ] as const)("audio in status %s with transcript %j", (status, transcript, view) => {
    expect(transcriptView(audio({ status, transcript }))).toEqual(view);
  });

  test("the doctor listens to the record through the patient's diary path", () => {
    expect(audioUrl("p-1", "e-9")).toBe("/api/v1/patients/p-1/diary/e-9/audio");
  });

  test("a check-in shows the mood in words, its words and another medicine", () => {
    const daily = entry({
      kind: "daily",
      daily_on: "2026-10-19",
      mood_level: 3,
      mood_words: ["тревога", "усталость"],
      text: null,
      other_medicine_text: "Пила ромашковый чай на ночь",
    });
    expect(entryRows(daily)).toEqual([
      { label: "Настроение", value: "Ровно" },
      { label: "Слова к настроению", value: "тревога, усталость" },
      { label: "Другое лекарство", value: "Пила ромашковый чай на ночь" },
    ]);
  });

  test("a voice record does not repeat its transcript as text; a corrected text is shown", () => {
    const said = "Ночью опять просыпалась.";
    expect(ownText(entry({ kind: "voice", audio: audio({ transcript: said }), text: said }))).toBe(
      null,
    );
    expect(
      ownText(entry({ kind: "voice", audio: audio({ transcript: said }), text: "Ночью" })),
    ).toBe("Ночью");
    expect(ownText(entry({ text: "Спокойный день." }))).toBe("Спокойный день.");
    expect(ownText(entry({ text: null }))).toBeNull();
  });

  test("a long entry is cut to six lines", () => {
    expect(isLongText("Спокойный день.")).toBe(false);
    expect(isLongText("а".repeat(400))).toBe(true);
  });
});

describe("errors", () => {
  test("a period the API refuses is named as such, without «try again»", () => {
    const refused = { status: 422, code: "invalid_period", detail: "Period must be 7–90 days" };
    expect(diaryFailure(refused, "Не удалось загрузить дневник")).toEqual({
      text: "Период не подходит: выберите от 7 до 90 дней наблюдения, не позже сегодня",
      retry: false,
    });
  });

  test("any other failure offers to try again", () => {
    expect(diaryFailure({ status: 503 }, "Не удалось загрузить дневник")).toEqual({
      text: "Не удалось загрузить дневник",
      retry: true,
    });
    expect(diaryFailure(new TypeError("fetch"), "Не удалось загрузить отметки")).toEqual({
      text: "Не удалось загрузить отметки",
      retry: true,
    });
  });
});

describe("open an entry", () => {
  const page = [
    entry({ entry_id: "e19", occurred_at: "2026-10-19T09:10:00+03:00" }),
    entry({ entry_id: "e17", occurred_at: "2026-10-17T20:40:00+03:00" }),
  ];
  const byId = (id: string) => (list: readonly ReturnType<typeof entry>[]) =>
    list.find((e) => e.entry_id === id);

  test("a loaded entry is found", () => {
    expect(locateEntry(page, true, byId("e17"), "2026-10-17T20:40:00+03:00")).toEqual({
      kind: "found",
      entryId: "e17",
    });
  });

  test("an older entry is looked for on the next pages: the list goes newest first", () => {
    expect(locateEntry(page, true, byId("e09"), "2026-10-09T09:10:00+03:00")).toEqual({
      kind: "more",
    });
  });

  test("an entry the list has passed, or with no pages left, is not available", () => {
    expect(locateEntry(page, true, byId("hidden"), "2026-10-18T09:10:00+03:00")).toEqual({
      kind: "missing",
    });
    expect(locateEntry(page, false, byId("e09"), "2026-10-09T09:10:00+03:00")).toEqual({
      kind: "missing",
    });
  });

  test("without a known time the next pages are read until they end", () => {
    expect(locateEntry(page, true, byId("e09"), null)).toEqual({ kind: "more" });
    expect(locateEntry(page, false, byId("e09"), null)).toEqual({ kind: "missing" });
  });
});

describe("findings", () => {
  test("only diary findings that wait for the doctor, newest first", () => {
    const list = [
      finding("old"),
      finding("new", {}, { created_at: "2026-10-19T08:00:00Z" }),
      finding("chat", { source_type: "message" }),
      finding("done", {}, { status: "confirmed" }),
      finding("therapy", {}, { kind: "therapy.prescription" }),
    ];
    expect(diaryFindings(list).map((p) => p.id)).toEqual(["new", "old"]);
  });

  test("a finding names its record by the time of the record", () => {
    const view = findingView(
      finding("f", {
        summary: "нарушение сна",
        transcript: true,
        recorded_at: "2026-10-18T21:30:00+03:00",
      }),
    );
    expect(view).toEqual({
      title: "Нарушение сна",
      category: "Симптом",
      record: "голосовая запись 18.10, 21:30",
    });
  });

  test("an old finding without the record time takes the time of the proposal", () => {
    const view = findingView(
      finding("f", { category: "adherence" }, { created_at: "2026-10-14T22:10:00+03:00" }),
    );
    expect(view.record).toBe("запись в дневнике 14.10, 22:10");
  });

  test("a finding belongs to the entry whose text, other medicine or transcript it read", () => {
    const voice = entry({
      entry_id: "voice-18",
      kind: "voice",
      audio: audio({ transcript_source_id: "src-transcript" }),
      sources: [source("src-transcript", "transcript")],
    });
    const daily = entry({
      entry_id: "daily-18",
      kind: "daily",
      sources: [source("src-old-text"), source("src-medicine", "other_medicine")],
    });
    const sleep = finding("sleep", { source_id: "src-transcript" });
    const medicine = finding("medicine", { source_id: "src-medicine" });
    const earlier = finding("earlier", { source_id: "src-old-text" });
    const deleted = finding("deleted", { source_id: "src-of-a-deleted-entry" });

    const byFinding = findingEntries([sleep, medicine, earlier, deleted], [voice, daily]);
    expect(byFinding.get("sleep")?.entry_id).toBe("voice-18");
    expect(byFinding.get("medicine")?.entry_id).toBe("daily-18");
    expect(byFinding.get("earlier")?.entry_id).toBe("daily-18");
    expect(byFinding.has("deleted")).toBe(false);

    const byEntry = findingsByEntry([sleep, medicine, earlier, deleted], [voice, daily]);
    expect(byEntry.get("voice-18")?.map((f) => f.id)).toEqual(["sleep"]);
    expect(byEntry.get("daily-18")?.map((f) => f.id)).toEqual(["medicine", "earlier"]);
  });

  test("a finding opens its entry when loaded, or when the next pages may hold it", () => {
    const sleep = finding("f", { recorded_at: "2026-10-18T21:30:00+03:00" });
    expect(canOpenFinding(sleep, entry(), false, "2026-10-06")).toBe(true);
    expect(canOpenFinding(sleep, undefined, true, "2026-10-06")).toBe(true);
    // Нет следующих страниц — записи в периоде нет: пациент её скрыл или она раньше периода.
    expect(canOpenFinding(sleep, undefined, false, "2026-10-06")).toBe(false);
    // Запись не позже своей находки: находка до начала периода — и запись до него.
    expect(canOpenFinding(sleep, undefined, true, "2026-10-19")).toBe(false);
  });

  test("an entry links to its findings", () => {
    expect(findingLabel(finding("f", { summary: "нарушение сна" }))).toBe(
      "Предложение системы: нарушение сна",
    );
    expect(findingLabel(finding("g", { category: "side_effect" }))).toBe(
      "Предложение системы: побочный эффект",
    );
  });
});
