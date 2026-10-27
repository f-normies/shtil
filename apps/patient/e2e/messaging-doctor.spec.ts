import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, test } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

const DOCTOR = user("doctor");
const PATIENT_ID = "00000000-0000-4000-8000-0000000000e1";
const PATIENT: Schemas["PatientSummary"] = {
  id: PATIENT_ID,
  access: "lead",
  full_name: "Синтетическая пациентка",
  email: "patient@example.invalid",
  lead_doctor_id: DOCTOR.id,
  created_at: "2026-10-20T09:00:00Z",
  phone: null,
  birth_date: "1990-03-12",
  sex: "female",
  pregnant: null,
  breastfeeding: null,
  child_pugh: null,
  egfr: null,
  egfr_measured_on: null,
};

function message(seq: number, senderId: string, text: string): Schemas["MessageOut"] {
  return {
    id: `message-${seq}`,
    client_message_id: `client-${seq}`,
    created_at: `2026-10-25T09:0${seq}:00Z`,
    duration_ms: null,
    kind: "text",
    sender_id: senderId,
    seq,
    text,
    transcription_status: null,
    transcription_text: null,
    transcription_error_code: null,
    audio_mime: null,
    audio_size: null,
    analysis_status: null,
    analysis_skip_reason: null,
  };
}

async function mockDoctorApi(context: BrowserContext) {
  const state = {
    messages: [message(1, PATIENT_ID, "Здравствуйте, доктор.")],
    sentTexts: [] as string[],
    readThrough: [] as number[],
    unread: 1,
    offline: false,
  };
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/v1/patients") return route.fulfill({ json: [PATIENT] });
    if (path === `/api/v1/patients/${PATIENT_ID}`) return route.fulfill({ json: PATIENT });
    if (path === "/api/v1/messaging/unread") {
      return route.fulfill({
        json: state.unread
          ? [
              {
                patient_id: PATIENT_ID,
                doctor_id: DOCTOR.id,
                unread_count: state.unread,
                last_message_id: "message-1",
                last_message_at: "2026-10-25T09:01:00Z",
              },
            ]
          : [],
      });
    }
    if (path.endsWith("/messaging-status")) {
      return route.fulfill({
        json: {
          patient_id: PATIENT_ID,
          doctor_id: DOCTOR.id,
          peer_full_name: PATIENT.full_name,
          online: true,
          last_seen_at: "2026-10-25T09:00:00Z",
          typing_until: null,
        } satisfies Schemas["MessagingStatusOut"],
      });
    }
    if (path === `/api/v1/patients/${PATIENT_ID}/messages` && request.method() === "GET") {
      return route.fulfill({
        json: {
          doctor_id: DOCTOR.id,
          items: state.messages,
          last_seq: state.messages.at(-1)?.seq ?? 0,
          next_after_seq: null,
          next_before_seq: null,
          peer_read_through_seq: 0,
          read_through_seq: state.readThrough.at(-1) ?? 0,
          unread_count: state.unread,
        } satisfies Schemas["MessageHistoryOut"],
      });
    }
    if (path === `/api/v1/patients/${PATIENT_ID}/messages` && request.method() === "POST") {
      if (state.offline) return route.abort("failed");
      const body = request.postDataJSON() as { client_message_id: string; text: string };
      state.sentTexts.push(body.text);
      const sent = {
        ...message(state.messages.length + 1, DOCTOR.id, body.text),
        client_message_id: body.client_message_id,
      };
      state.messages.push(sent);
      return route.fulfill({ status: 201, json: sent });
    }
    if (path === `/api/v1/patients/${PATIENT_ID}/messages/read`) {
      const body = request.postDataJSON() as { through_seq: number };
      state.readThrough.push(body.through_seq);
      state.unread = 0;
      return route.fulfill({ json: { read_through_seq: body.through_seq } });
    }
    if (
      path === "/api/v1/alerts" ||
      /\/(alerts|diagnoses|prescriptions|documents|medications|reactions|proposals)$/.test(path)
    ) {
      return route.fulfill({ json: [] });
    }
    if (path.endsWith("/assessments")) {
      return route.fulfill({ json: { items: [], next_cursor: null } });
    }
    if (path.endsWith("/assessments/schedule")) {
      return route.fulfill({ json: { today: "2026-10-25", items: [] } });
    }
    if (path === "/api/v1/proposals/summary") {
      return route.fulfill({ json: { pending: 0, decided_today: 0 } });
    }
    await route.fallback();
  });
  return state;
}

test("doctor chat opens in the inspector, preserves local sends offline, and marks unread as read", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "doctor");
  const state = await mockDoctorApi(page.context());
  await page.goto(`/doctor/patients/${PATIENT_ID}`);

  const opener = page.getByRole("button", { name: /Написать/ });
  await expect(opener).toBeVisible();
  await expect(opener).toContainText("1");
  await opener.click();

  const inspector = page.getByRole("complementary", { name: "Сообщения" });
  const chat = inspector.getByRole("region", { name: `Чат с ${PATIENT.full_name}` });
  await expect(chat).toBeVisible();
  await expect(chat.getByText("Здравствуйте, доктор.")).toBeVisible();
  await expect.poll(() => state.readThrough.at(-1)).toBe(1);

  await page.getByRole("radio", { name: "Шкалы" }).click();
  await expect(page).toHaveURL(new RegExp(`/patients/${PATIENT_ID}/scales$`));
  await expect(inspector).toBeVisible();

  const input = chat.getByRole("textbox", { name: "Сообщение пациентке" });
  const inspectorBounds = await inspector.boundingBox();
  const chatBounds = await chat.boundingBox();
  const composerBounds = await input.boundingBox();
  expect(inspectorBounds).not.toBeNull();
  expect(chatBounds).not.toBeNull();
  expect(composerBounds).not.toBeNull();
  expect(chatBounds!.x).toBeGreaterThanOrEqual(inspectorBounds!.x);
  expect(chatBounds!.x + chatBounds!.width).toBeLessThanOrEqual(
    inspectorBounds!.x + inspectorBounds!.width,
  );
  expect(composerBounds!.x).toBeGreaterThanOrEqual(chatBounds!.x);
  expect(composerBounds!.x + composerBounds!.width).toBeLessThanOrEqual(
    chatBounds!.x + chatBounds!.width,
  );
  expect(composerBounds!.y + composerBounds!.height).toBeGreaterThan(
    inspectorBounds!.y + inspectorBounds!.height - 100,
  );
  await input.fill("Сообщение отправлено.");
  await chat.getByRole("button", { name: "Отправить" }).click();
  await expect(chat.getByText("Сообщение отправлено.")).toBeVisible();
  await expect.poll(() => state.sentTexts).toContain("Сообщение отправлено.");
  const horizontalLayout = await chat.evaluate((section) => {
    const actions = section.querySelector<HTMLElement>(".ui-chat__composer-actions");
    return {
      chatScrollLeft: section.scrollLeft,
      chatScrollWidth: section.scrollWidth,
      chatClientWidth: section.clientWidth,
      actions: actions && {
        scrollWidth: actions.scrollWidth,
        clientWidth: actions.clientWidth,
        children: Array.from(actions.children).map((child) => {
          const element = child as HTMLElement;
          const style = getComputedStyle(element);
          return {
            className: element.className,
            minWidth: style.minWidth,
            width: element.getBoundingClientRect().width,
          };
        }),
      },
    };
  });
  expect(horizontalLayout.chatScrollLeft, JSON.stringify(horizontalLayout)).toBeLessThan(8);
  expect(
    horizontalLayout.actions!.scrollWidth - horizontalLayout.actions!.clientWidth,
    JSON.stringify(horizontalLayout),
  ).toBeLessThan(8);
  await page.screenshot({ path: "/private/tmp/cc33-doctor.png" });

  await page.context().setOffline(true);
  state.offline = true;
  await input.fill("Останется в очереди.");
  await input.press("Enter");
  await expect(chat.getByText("Останется в очереди.")).toBeVisible();
  await expect(chat.getByText("Отправится при подключении")).toBeVisible();

  await inspector.getByRole("button", { name: "Закрыть" }).click();
  await expect(opener).toBeFocused();
  await opener.click();
  await expect(inspector.getByText("Останется в очереди.")).toBeVisible();

  state.offline = false;
  await page.context().setOffline(false);
  await expect.poll(() => state.sentTexts).toContain("Останется в очереди.");
  await expect(opener).not.toContainText("1");
});
