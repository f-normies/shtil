import type { Schemas } from "@compass/api-client";
import { expect, test } from "@playwright/test";
import { mockAssessmentApi, SYNTHETIC_USERS } from "./assessment-api-mock";
import { PATIENT_A } from "./assessment-fixtures";

const ENTRY_ID = "00000000-0000-4000-8000-0000000000f1";
const OPERATION_ID = "00000000-0000-4000-8000-0000000000f2";
const PRESCRIPTION_ID = "00000000-0000-4000-8000-0000000000f3";
const MESSAGE_ID = "00000000-0000-4000-8000-0000000000f4";

test("shared patient shell uses generated diary APIs and retries queued entries on route changes", async ({
  page,
  context,
}) => {
  const api = await mockAssessmentApi(context, { user: null });
  const overviewRequests: Array<{ path: string; search: string; method: string }> = [];
  const postedOperations: string[] = [];
  let completedPosts = 0;
  const diaryContext: Schemas["DiaryContextOut"] = {
    daily_status: "missing",
    month_entry_count: 2,
    observation_from: "2026-10-01",
    server_now: "2026-10-23T10:00:00Z",
    timezone: "Europe/Moscow",
    today: "2026-10-23",
  };
  const schedule: Schemas["ScheduleOut"] = {
    today: "2026-10-23",
    items: [{ scale_id: "synthetic-scale", scale_title: "Synthetic scale", due_on: "2026-10-23" }],
  };
  const prescriptions: Schemas["ScheduleItem"][] = [
    {
      appearance: null,
      appearance_origin: "unknown",
      appearance_revision: 0,
      default_appearance: null,
      prescription_id: PRESCRIPTION_ID,
      trade_name: "Синтетин",
      inn: "синтетин",
      dose_mg: 20,
      times_per_day: 1,
      schedule: "утром",
      started_at: "2026-10-11T09:00:00Z",
      ends_on: null,
      instructions: [],
      reactions: [],
    },
  ];
  const messages: Schemas["MessageHistoryOut"] = {
    doctor_id: "00000000-0000-4000-8000-0000000000f5",
    items: [
      {
        analysis_skip_reason: null,
        analysis_status: null,
        client_message_id: MESSAGE_ID,
        created_at: "2026-10-23T09:00:00Z",
        duration_ms: null,
        id: MESSAGE_ID,
        kind: "text",
        sender_id: "00000000-0000-4000-8000-0000000000f5",
        seq: 1,
        text: "Синтетическое сообщение врача",
        transcription_error_code: null,
        transcription_status: null,
        transcription_text: null,
      },
    ],
    last_seq: 1,
    next_after_seq: null,
    next_before_seq: null,
    peer_read_through_seq: 0,
    read_through_seq: 1,
    unread_count: 0,
  };

  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (method === "GET" && url.pathname.startsWith("/api/v1/diary/context")) {
      overviewRequests.push({ path: url.pathname, search: url.search, method });
      await route.fulfill({ status: 200, json: diaryContext });
      return;
    }
    if (method === "GET" && url.pathname === "/api/v1/assessments/schedule") {
      overviewRequests.push({ path: url.pathname, search: url.search, method });
      await route.fulfill({ status: 200, json: schedule });
      return;
    }
    if (method === "GET" && url.pathname === "/api/v1/me/prescriptions") {
      overviewRequests.push({ path: url.pathname, search: url.search, method });
      await route.fulfill({ status: 200, json: prescriptions });
      return;
    }
    if (method === "GET" && url.pathname === `/api/v1/patients/${PATIENT_A}/messages`) {
      overviewRequests.push({ path: url.pathname, search: url.search, method });
      await route.fulfill({ status: 200, json: messages });
      return;
    }
    if (method === "POST" && url.pathname === "/api/v1/diary/entries") {
      const payload = request.postDataJSON() as Schemas["DiaryEntryCreateIn"];
      postedOperations.push(payload.operation_id);
      if (postedOperations.length === 1) {
        await route.fulfill({ status: 503, json: { status: 503, title: "Synthetic outage" } });
      } else {
        await route.fulfill({
          status: 201,
          json: {
            entry_id: payload.entry_id,
            operation_id: payload.operation_id,
            hidden: false,
            received_at: "2026-10-23T10:01:00Z",
            revision: 1,
          } satisfies Schemas["DiaryCommandReceipt"],
        });
      }
      completedPosts += 1;
      return;
    }
    await route.fallback();
  });

  await page.goto("/patient/login");
  await page.evaluate(
    async ({ ownerId, entryId, operationId }) => {
      const { AssessmentStore } = await import("/patient/src/assessments/store.ts");
      const { DiaryStore } = await import("/patient/src/diary/store.ts");
      await (await AssessmentStore.open()).rememberOnlinePatient({ id: ownerId, role: "patient" });
      await (await DiaryStore.open()).enqueueEntry({
        ownerId,
        payload: {
          entry_id: entryId,
          operation_id: operationId,
          kind: "text",
          occurred_at: "2026-10-23T10:00:00Z",
          text: "Проверка синхронизации из общего shell",
        },
      });
    },
    { ownerId: PATIENT_A, entryId: ENTRY_ID, operationId: OPERATION_ID },
  );
  api.setAuthenticatedUser(SYNTHETIC_USERS.a);

  await page.goto("/patient/");
  await expect(
    page.getByRole("heading", { name: "Здравствуйте, Синтетический пациент А" }),
  ).toBeVisible();
  await expect.poll(() => overviewRequests.length).toBe(4);
  await expect.poll(() => completedPosts).toBe(1);
  expect(overviewRequests).toEqual(
    expect.arrayContaining([
      { path: "/api/v1/diary/context", search: "", method: "GET" },
      { path: "/api/v1/assessments/schedule", search: "", method: "GET" },
      { path: "/api/v1/me/prescriptions", search: "", method: "GET" },
      { path: `/api/v1/patients/${PATIENT_A}/messages`, search: "?limit=1", method: "GET" },
    ]),
  );

  await expect
    .poll(async () =>
      page.evaluate(async (ownerId) => {
        const { DiaryStore } = await import("/patient/src/diary/store.ts");
        return (await DiaryStore.open()).getContextCache(ownerId);
      }, PATIENT_A),
    )
    .toMatchObject({
      owner_id: PATIENT_A,
      context: { today: "2026-10-23", timezone: "Europe/Moscow" },
      schedule_snapshot: { today: "2026-10-23", items: [{ scale_id: "synthetic-scale" }] },
      prescriptions_snapshot: [{ prescription_id: PRESCRIPTION_ID }],
      latest_message: { id: MESSAGE_ID, text: "Синтетическое сообщение врача" },
    });

  await page.getByRole("link", { name: "Открыть схему приёма" }).click();
  await expect(page.getByRole("heading", { name: "Моя терапия" })).toBeVisible();
  await expect.poll(() => completedPosts).toBe(2);
  expect(postedOperations).toEqual([OPERATION_ID, OPERATION_ID]);
  const durableState = await page.evaluate(async (ownerId) => {
    const { DiaryStore } = await import("/patient/src/diary/store.ts");
    const store = await DiaryStore.open();
    return {
      outbox: await store.getOutboxForOwner(ownerId),
      entries: await store.listEntries(ownerId),
    };
  }, PATIENT_A);
  expect(durableState.outbox).toEqual([]);
  expect(durableState.entries).toMatchObject([{ entry_id: ENTRY_ID, sync_state: "synced" }]);
});
