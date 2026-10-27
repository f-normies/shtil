import type { Schemas } from "@compass/api-client";
import type { BrowserContext, Page, TestInfo } from "@playwright/test";
import { LEBEDEVA } from "./doctor-window-mock";
import { SYNTHETIC_WEBM_BASE64 } from "./synthetic-webm";

/**
 * Подмена ручек дневника #31 для вкладки «Дневник» врача (спека #115). Пациентка и записи
 * выдуманы: Лебедева, наблюдение с 22.09, сегодня 19.10.2026. Время — московское, как
 * у сервера (`settings.timezone`).
 */

declare const process: { env: Record<string, string | undefined> };

type Entry = Schemas["DiaryDoctorEntryOut"];
type Mood = Schemas["DiaryMoodPoint"];

export const DIARY = `/api/v1/patients/${LEBEDEVA}/diary`;
const OBSERVATION_START = "2026-09-22";
const WEBM = Uint8Array.from(atob(SYNTHETIC_WEBM_BASE64), (c) => c.charCodeAt(0));
// 22.09–5.10 и 6–19.10, как в кадре 06; пропуски 30.09 и 11.10.
// biome-ignore format: уровни по дням читаются строкой
const LEVELS = [2, 2, 1, 2, 3, 2, 2, 1, null, 2, 3, 2, 2, 3, 2, 2, 3, 2, 3, null, 3, 3, 2, 3, 4, 4, 2, 3];

/** Что видит и делает подмена: запросы к дневнику, готова ли расшифровка, отказ в периоде. */
export type DiaryMock = { asked: URL[]; transcribed: boolean; refused: boolean; empty: boolean };

export const diaryMock = (fields: Partial<DiaryMock> = {}): DiaryMock => ({
  asked: [],
  transcribed: false,
  refused: false,
  empty: false,
  ...fields,
});

function addDays(day: string, n: number): string {
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + n);
  return date.toISOString().slice(0, 10);
}

function moods(): Mood[] {
  return LEVELS.flatMap((level, i) => {
    const day = addDays(OBSERVATION_START, i);
    return level === null
      ? []
      : [
          {
            entry_id: `daily-${day}`,
            occurred_at: `${day}T09:10:00+03:00`,
            daily_on: day,
            mood_level: level,
            mood_words: [],
          },
        ];
  }).reverse();
}

function entry(id: string, at: string, fields: Partial<Entry>): Entry {
  return {
    entry_id: id,
    kind: "text",
    occurred_at: `${at}+03:00`,
    daily_on: null,
    revision: 1,
    received_at: `${at}+03:00`,
    mood_level: null,
    mood_words: [],
    text: null,
    other_medicine_text: null,
    audio: null,
    source_statuses: [],
    sources: [],
    ...fields,
  };
}

function voice(id: string, status: "processing" | "done", ms: number, transcript: string | null) {
  return {
    id: `audio-${id}`,
    status,
    duration_ms: ms,
    transcript,
    transcript_source_id: transcript ? `src-${id}` : null,
  };
}

const source = (id: string, field: "text" | "transcript") => ({
  field,
  source_id: id,
  revision: 1,
  status: "done" as const,
  proposals: [],
  findings: [],
});

export const MORNING_VOICE = "Утром снова тревожно, но на работу пошла.";

function firstPage(transcribed: boolean): Entry[] {
  return [
    entry("voice-19", "2026-10-19T09:31:00", {
      kind: "voice",
      audio: transcribed
        ? voice("voice-19", "done", 84_000, MORNING_VOICE)
        : voice("voice-19", "processing", 84_000, null),
    }),
    entry("daily-2026-10-19", "2026-10-19T09:10:00", {
      kind: "daily",
      daily_on: "2026-10-19",
      mood_level: 3,
      mood_words: ["тревога"],
      text: "Спала лучше, но с утра опять тревожно перед работой.",
    }),
    entry("voice-18", "2026-10-18T21:30:00", {
      kind: "voice",
      audio: voice(
        "voice-18",
        "done",
        42_000,
        "Ночью опять просыпалась, потом долго не могла уснуть.",
      ),
      sources: [source("src-voice-18", "transcript")],
    }),
    entry("daily-2026-10-18", "2026-10-18T21:05:00", {
      kind: "daily",
      daily_on: "2026-10-18",
      mood_level: 2,
    }),
    entry("mood-18", "2026-10-18T16:02:00", { kind: "mood", mood_level: 2 }),
    entry("text-17", "2026-10-17T20:40:00", { text: "Спокойный день, гуляли с дочерью." }),
  ];
}

// Вторая страница: 14.10 — запись о пропуске дозы и ежедневная отметка. Остальные отметки
// графика без записей: пациент их скрыл, «Открыть запись» отвечает «Запись недоступна».
const SECOND_PAGE: Entry[] = [
  entry("text-14", "2026-10-14T22:10:00", {
    text: "Вечером уснула и не выпила таблетку.",
    sources: [source("src-text-14", "text")],
  }),
  entry("daily-2026-10-14", "2026-10-14T09:10:00", {
    kind: "daily",
    daily_on: "2026-10-14",
    mood_level: 2,
  }),
];

function finding(id: string, at: string, payload: Record<string, unknown>) {
  return {
    id,
    patient_id: LEBEDEVA,
    kind: "freetext.finding",
    origin_type: "ai_run" as const,
    origin_ref: "run",
    status: "proposed" as const,
    created_at: at,
    decided_by: null,
    decided_at: null,
    decision_comment: null,
    payload: { source_type: "diary", recorded_at: at, ...payload },
  };
}

const FINDINGS = [
  finding("f-sleep", "2026-10-18T21:30:00+03:00", {
    source_id: "src-voice-18",
    transcript: true,
    category: "symptom",
    summary: "нарушение сна",
    quote: "Ночью опять просыпалась",
  }),
  finding("f-dose", "2026-10-14T22:10:00+03:00", {
    source_id: "src-text-14",
    transcript: false,
    category: "adherence",
    summary: "пропуск вечерней дозы",
    quote: "не выпила таблетку",
  }),
];

const inPeriod = (at: string, url: URL) => {
  const day = at.slice(0, 10);
  return day >= (url.searchParams.get("start") ?? "") && day <= (url.searchParams.get("end") ?? "");
};

const REFUSED = {
  type: "about:blank",
  title: "Unprocessable Content",
  status: 422,
  detail: "Period must be 7–90 days within observation and today",
  code: "invalid_period",
};

/** Ручки дневника, голос и находки пациентки. */
export async function mockDiary(context: BrowserContext, mock: DiaryMock) {
  await context.route(`**/api/v1/patients/${LEBEDEVA}/proposals`, (route) =>
    route.fulfill({ json: FINDINGS }),
  );
  await context.route(`**${DIARY}**`, async (route) => {
    const url = new URL(route.request().url());
    mock.asked.push(url);
    if (url.pathname.endsWith("/audio")) {
      await route.fulfill({
        status: 200,
        contentType: "audio/webm",
        headers: { "Accept-Ranges": "bytes" },
        body: WEBM,
      });
      return;
    }
    if (mock.refused) {
      await route.fulfill({ status: 422, contentType: "application/problem+json", json: REFUSED });
      return;
    }
    if (url.pathname === `${DIARY}/moods`) {
      const items = mock.empty ? [] : moods().filter((m) => inPeriod(m.occurred_at, url));
      await route.fulfill({ json: { items } });
      return;
    }
    const second = url.searchParams.get("cursor") === "page-2";
    const page = second ? SECOND_PAGE : firstPage(mock.transcribed);
    const items = mock.empty ? [] : page.filter((e) => inPeriod(e.occurred_at, url));
    await route.fulfill({ json: { items, next_cursor: second || mock.empty ? null : "page-2" } });
  });
}

/** Скриншот на 1280 px в `DIARY_SCREENS`, без него — в папку результатов теста. */
export async function shoot(page: Page, testInfo: TestInfo, name: string) {
  const dir = process.env.DIARY_SCREENS;
  await page.setViewportSize({ width: 1280, height: 2100 });
  // Поддельные часы теста держат CSS-анимации в начале: снимок доводит их до конца.
  const path = dir ? `${dir}/${name}` : testInfo.outputPath(name);
  await page.screenshot({ path, animations: "disabled" });
  await page.setViewportSize({ width: 1280, height: 832 });
}

export const lastAsked = (mock: DiaryMock, path: string) =>
  mock.asked.filter((u) => u.pathname === path).at(-1)?.searchParams;
