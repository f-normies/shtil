/**
 * Синтетические данные только для тестов дневника: отметки Лебедевой из аннотации «График
 * настроения: правила для разработки» (кадр 06 и доска состояний). Сегодня — 19.10.2026,
 * наблюдение с 22.09. Время — с поясом сервера (+03:00): дни считаются по Москве.
 * Формы — как у ручек #31: `DiaryMoodPoint` и `DiaryDoctorEntryOut`.
 */
import type { Schemas } from "@compass/api-client";
import { addDays, type Day } from "./days";

type MoodPoint = Schemas["DiaryMoodPoint"];
type DiaryEntry = Schemas["DiaryDoctorEntryOut"];
type Audio = Schemas["DiaryAudioOut"];
type Proposal = Schemas["ProposalOut"];

export const TODAY: Day = "2026-10-19";
export const OBSERVATION_START: Day = "2026-09-22";
/** Быстрая отметка настроения: у неё нет дня ежедневной отметки. */
export const QUICK = { daily_on: null } as const;

// 22.09–5.10: тяжело, тяжело, очень тяжело, тяжело, ровно, тяжело, тяжело, очень тяжело,
// пропуск 30.09, тяжело, ровно, тяжело, тяжело, ровно. 6–19.10 — как в кадре 06.
const UNTIL_OCT_5 = [2, 2, 1, 2, 3, 2, 2, 1, null, 2, 3, 2, 2, 3];
const FROM_OCT_6 = [2, 2, 3, 2, 3, null, 3, 3, 2, 3, 4, 4, 2, 3];
const LEVELS = [...UNTIL_OCT_5, ...FROM_OCT_6];

/** Отметка из ежедневной отметки дня `at`; быстрая — с `QUICK`. */
export function mark(at: string, level: number, overrides: Partial<MoodPoint> = {}): MoodPoint {
  return {
    entry_id: `e-${at}`,
    occurred_at: at,
    daily_on: at.slice(0, 10),
    mood_level: level,
    mood_words: [],
    ...overrides,
  };
}

/** Одна ежедневная отметка в 09:10 на каждый день наблюдения, кроме 30.09 и 11.10. */
export function lebedevaMarks(): MoodPoint[] {
  return LEVELS.flatMap((level, i) =>
    level === null ? [] : [mark(`${addDays(OBSERVATION_START, i)}T09:10:00+03:00`, level)],
  );
}

export function audio(overrides: Partial<Audio> = {}): Audio {
  return {
    id: "a1",
    status: "done",
    duration_ms: 84_000,
    transcript: "Ночью опять просыпалась, потом долго не могла уснуть.",
    transcript_source_id: "src-transcript",
    ...overrides,
  };
}

export function entry(overrides: Partial<DiaryEntry> = {}): DiaryEntry {
  return {
    entry_id: "e1",
    kind: "text",
    occurred_at: "2026-10-17T20:40:00+03:00",
    daily_on: null,
    revision: 1,
    received_at: "2026-10-17T20:40:05+03:00",
    mood_level: null,
    mood_words: [],
    text: "Спокойный день, гуляли с дочерью.",
    other_medicine_text: null,
    audio: null,
    source_statuses: [],
    sources: [],
    ...overrides,
  };
}

/** Источник разбора записи: по нему находка `payload.source_id` связана с записью. */
export function source(
  sourceId: string,
  field: DiaryEntry["sources"][number]["field"] = "text",
): DiaryEntry["sources"][number] {
  return { field, source_id: sourceId, revision: 1, status: "done", proposals: [], findings: [] };
}

/** Находка разбора дневника (`freetext.finding`), как её пишет #32. */
export function finding(
  id: string,
  payload: Record<string, unknown> = {},
  overrides: Partial<Proposal> = {},
): Proposal {
  return {
    id,
    patient_id: "p1",
    kind: "freetext.finding",
    origin_type: "ai_run",
    origin_ref: "run",
    status: "proposed",
    created_at: "2026-10-18T18:31:00Z",
    decided_by: null,
    decided_at: null,
    decision_comment: null,
    payload: {
      source_type: "diary",
      source_id: "src-transcript",
      category: "symptom",
      ...payload,
    },
    ...overrides,
  };
}
