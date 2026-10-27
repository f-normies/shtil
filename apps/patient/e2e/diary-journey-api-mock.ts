import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, type Route } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { PATIENT_A } from "./assessment-fixtures";

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function moscowDay(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (name: string) => parts.find((value) => value.type === name)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

export type DiaryJourneyApiOptions = {
  monthEntryCount?: number;
  serverNow?: string;
  user?: Schemas["UserOut"];
};

export type DiaryJourneyApiMock = { setNetworkAvailable: (available: boolean) => void };

/** Synthetic diary endpoints for patient journey tests. */
export async function mockDiaryJourneyApi(
  context: BrowserContext,
  options: DiaryJourneyApiOptions = {},
): Promise<DiaryJourneyApiMock> {
  await mockAssessmentApi(context, options.user ? { user: options.user } : {});
  let networkAvailable = true;
  const serverNow = options.serverNow ?? new Date().toISOString();
  const today = moscowDay(new Date(serverNow));
  const diaryContext: Schemas["DiaryContextOut"] = {
    daily_status: "missing",
    month_entry_count: options.monthEntryCount ?? 0,
    observation_from: `${today.slice(0, 7)}-01`,
    server_now: serverNow,
    timezone: "Europe/Moscow",
    today,
  };
  const schedule: Schemas["ScheduleOut"] = { today, items: [] };

  await context.route("**/api/**", async (route) => {
    if (!networkAvailable) {
      await route.abort("failed");
      return;
    }
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/v1/diary/context") {
      await json(route, 200, diaryContext);
    } else if (request.method() === "GET" && url.pathname === "/api/v1/assessments/schedule") {
      await json(route, 200, schedule);
    } else if (request.method() === "GET" && url.pathname === "/api/v1/me/prescriptions") {
      await json(route, 200, []);
    } else if (
      request.method() === "GET" &&
      url.pathname === `/api/v1/patients/${PATIENT_A}/messages`
    ) {
      await json(route, 200, {
        doctor_id: "00000000-0000-4000-8000-0000000000d5",
        items: [],
        last_seq: 0,
        next_after_seq: null,
        next_before_seq: null,
        peer_read_through_seq: 0,
        read_through_seq: 0,
        unread_count: 0,
      } satisfies Schemas["MessageHistoryOut"]);
    } else if (request.method() === "GET" && url.pathname === "/api/v1/diary/entries") {
      await json(route, 200, { items: [], next_cursor: null } satisfies Schemas["DiaryEntryPage"]);
    } else if (request.method() === "GET" && url.pathname === "/api/v1/diary/intake") {
      await json(route, 200, { items: [] } satisfies Schemas["IntakeMarksOut"]);
    } else if (request.method() === "POST" && url.pathname.startsWith("/api/v1/diary/")) {
      await json(route, 503, { status: 503, title: "Synthetic offline queue" });
    } else {
      await route.fallback();
    }
  });

  return { setNetworkAvailable: (available) => (networkAvailable = available) };
}

/** Wait for the app's real online-session guard to persist the synthetic owner pointer. */
export async function waitForDiaryOwner(page: Page, ownerId = PATIENT_A): Promise<void> {
  await expect
    .poll(async () =>
      page.evaluate(async () => {
        const { getAssessmentStore } = await import("/patient/src/session.ts");
        return (await getAssessmentStore()).getOwnerPointer();
      }),
    )
    .toMatchObject({ id: ownerId, role: "patient", locked: false });
}
