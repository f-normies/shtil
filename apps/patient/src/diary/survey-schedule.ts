import type { DiaryScheduleSnapshot, DiarySurveyStatus } from "./types";

export type DiaryScheduleState = {
  status: DiarySurveyStatus;
  dueScaleIds: string[];
  availableScaleIds: string[];
  needsRefresh: boolean;
};

export type DiaryScheduleFreshness = {
  nowMs?: number;
  latestRequestFailed?: boolean;
};

const SCHEDULE_STALE_TIME_MS = 30_000;

/** Use only a successful same-day server schedule to decide that no survey is due. */
export function resolveDiarySchedule(
  snapshot: DiaryScheduleSnapshot | null,
  today: string | null,
  cachedScaleIds: readonly string[],
  freshness: DiaryScheduleFreshness = {},
): DiaryScheduleState {
  if (
    !snapshot ||
    !today ||
    snapshot.today !== today ||
    !Number.isFinite(Date.parse(snapshot.fetched_at))
  ) {
    return { status: "unknown", dueScaleIds: [], availableScaleIds: [], needsRefresh: true };
  }

  const fetchedAt = Date.parse(snapshot.fetched_at);
  const ageMs = (freshness.nowMs ?? Date.now()) - fetchedAt;
  const isFresh = !freshness.latestRequestFailed && ageMs >= 0 && ageMs < SCHEDULE_STALE_TIME_MS;

  const dueScaleIds = snapshot.items
    .filter((item) => item.due_on <= snapshot.today)
    .map((item) => item.scale_id);
  if (dueScaleIds.length === 0) {
    return isFresh
      ? { status: "not_due", dueScaleIds, availableScaleIds: [], needsRefresh: false }
      : { status: "unknown", dueScaleIds, availableScaleIds: [], needsRefresh: true };
  }

  const cached = new Set(cachedScaleIds);
  return {
    status: "pending",
    dueScaleIds,
    availableScaleIds: dueScaleIds.filter((scaleId) => cached.has(scaleId)),
    needsRefresh: !isFresh,
  };
}
