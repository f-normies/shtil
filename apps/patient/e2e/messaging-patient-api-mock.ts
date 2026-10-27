import type { Schemas } from "@compass/api-client";
import type { BrowserContext, Route, WebSocketRoute } from "@playwright/test";

export const PATIENT_ID = "00000000-0000-4000-8000-0000000000d1";
export const DOCTOR_ID = "00000000-0000-4000-8000-0000000000d2";
export const DOCTOR_NAME = "Синтетический лечащий врач";

const EMPTY_DAY_DOSES: Schemas["DayDosesOut"] = { day: "2026-10-25", doses: [] };

type TextSend = {
  client_message_id: string;
  expected_doctor_id: string;
  text: string;
};

type Message = {
  id: string;
  client_message_id: string;
  sender_id: string;
  seq: number;
  kind: "text" | "voice";
  text: string | null;
  created_at: string;
  duration_ms: number | null;
  audio_mime?: string;
  audio_size?: number;
  analysis_status: "processing" | "done" | null;
  analysis_skip_reason: null;
  transcription_status: "queued" | "processing" | "done" | null;
  transcription_text: string | null;
  transcription_error_code: null;
};

type VoiceUpload = {
  contentType: string;
  bodyBytes: number;
  expectedDoctorId: string;
  clientMessageId: string;
};

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function syntheticMessage(input: TextSend, seq: number): Message {
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    client_message_id: input.client_message_id,
    sender_id: PATIENT_ID,
    seq,
    kind: "text",
    text: input.text,
    created_at: "2026-10-25T12:00:00Z",
    duration_ms: null,
    audio_mime: undefined,
    audio_size: undefined,
    analysis_status: null,
    analysis_skip_reason: null,
    transcription_status: null,
    transcription_text: null,
    transcription_error_code: null,
  };
}

function syntheticVoiceMessage(input: VoiceUpload, seq: number): Message {
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    client_message_id: input.clientMessageId,
    sender_id: PATIENT_ID,
    seq,
    kind: "voice",
    text: null,
    created_at: "2026-10-25T12:00:00Z",
    duration_ms: 1_000,
    audio_mime: "audio/webm",
    audio_size: Math.max(1, input.bodyBytes),
    analysis_status: "done",
    analysis_skip_reason: null,
    transcription_status: "done",
    transcription_text: "Синтетическая расшифровка.",
    transcription_error_code: null,
  };
}

/** Synthetic patient and messaging API. It can fail or hold a text-send response. */
export async function mockMessagingPatientApi(
  context: BrowserContext,
  options: { unauthenticated?: boolean } = {},
) {
  let authenticated = !options.unauthenticated;
  let failNextSessionCheck = false;
  let failNextSend = false;
  let holdNextSuccessfulSend = false;
  let heldSendStartedResolve: () => void = () => {};
  let releaseHeldSendResolve: () => void = () => {};
  let heldSendStarted = Promise.resolve();
  let heldSendResponse = Promise.resolve();
  const messages: Message[] = [];
  const sendAttempts: TextSend[] = [];
  const voiceUploads: VoiceUpload[] = [];
  const typingCommands: Array<{
    type: "message.typing";
    data: { patient_id: string; expected_doctor_id: string; active: boolean };
  }> = [];
  const patientMessageRequests: Array<{ method: string; path: string }> = [];
  const sockets: WebSocketRoute[] = [];

  await context.routeWebSocket("**/api/v1/ws", (socket) => {
    sockets.push(socket);
    socket.send(JSON.stringify({ type: "ready", data: {} }));
    socket.onMessage((data) => {
      try {
        const event = JSON.parse(String(data)) as unknown;
        if (
          event &&
          typeof event === "object" &&
          "type" in event &&
          event.type === "message.typing" &&
          "data" in event &&
          event.data &&
          typeof event.data === "object"
        ) {
          typingCommands.push(event as (typeof typingCommands)[number]);
          socket.send(JSON.stringify({ type: "message.typing", data: {} }));
        }
      } catch {
        // Ignore malformed client frames in this synthetic API.
      }
    });
  });

  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (url.pathname.startsWith("/api/v1/patients/") && url.pathname.includes("/messages")) {
      patientMessageRequests.push({ method, path: url.pathname });
    }

    if (url.pathname === "/api/v1/auth/me") {
      if (failNextSessionCheck) {
        failNextSessionCheck = false;
        await route.abort("failed");
        return;
      }
      if (!authenticated) {
        await json(route, 401, { status: 401, title: "Сессия завершена" });
        return;
      }
      await json(route, 200, {
        id: PATIENT_ID,
        email: "patient@example.invalid",
        role: "patient",
        full_name: "Синтетический пациент",
      });
      return;
    }

    if (url.pathname === "/api/v1/auth/login" && method === "POST") {
      authenticated = true;
      await json(route, 200, {
        id: PATIENT_ID,
        email: "patient@example.invalid",
        role: "patient",
        full_name: "Синтетический пациент",
      });
      return;
    }

    if (url.pathname === "/api/v1/me/preferences") {
      await json(route, 200, {
        display_name: "Синтетический пациент",
        reminder_time: "21:00",
        mood_palette: "warm",
        story_mode: "voice",
        text_size: "normal",
        onboarding_completed_at: "2026-10-25T09:00:00Z",
        revision: 0,
        timezone: "Europe/Moscow",
        updated_at: null,
      });
      return;
    }
    if (url.pathname === "/api/v1/auth/logout" && method === "POST") {
      authenticated = false;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (url.pathname === `/api/v1/patients/${PATIENT_ID}/messaging-status`) {
      await json(route, 200, {
        patient_id: PATIENT_ID,
        doctor_id: DOCTOR_ID,
        peer_full_name: DOCTOR_NAME,
        online: true,
        last_seen_at: null,
        typing_until: null,
      });
      return;
    }

    if (url.pathname === `/api/v1/patients/${PATIENT_ID}/messages` && method === "GET") {
      const lastSeq = messages.at(-1)?.seq ?? 0;
      await json(route, 200, {
        doctor_id: DOCTOR_ID,
        items: [...messages],
        last_seq: lastSeq,
        next_after_seq: null,
        next_before_seq: null,
        peer_read_through_seq: 0,
        read_through_seq: 0,
        unread_count: 0,
      });
      return;
    }

    if (url.pathname === `/api/v1/patients/${PATIENT_ID}/messages` && method === "POST") {
      const input = request.postDataJSON() as TextSend;
      sendAttempts.push(input);
      if (failNextSend) {
        failNextSend = false;
        await route.abort("failed");
        return;
      }
      if (holdNextSuccessfulSend) {
        holdNextSuccessfulSend = false;
        heldSendStartedResolve();
        await heldSendResponse;
      }
      const message = syntheticMessage(input, messages.length + 1);
      messages.push(message);
      await json(route, 201, message);
      for (const socket of sockets) {
        socket.send(
          JSON.stringify({
            type: "message.created",
            data: { patient_id: PATIENT_ID, doctor_id: DOCTOR_ID, message },
          }),
        );
      }
      return;
    }

    if (url.pathname === `/api/v1/patients/${PATIENT_ID}/messages/voice` && method === "POST") {
      const formBody = request.postData() ?? "";
      const clientMessageId =
        formBody.match(/name="client_message_id"\r\n\r\n([^\r\n]+)/)?.[1] ?? "";
      const expectedDoctorId =
        formBody.match(/name="expected_doctor_id"\r\n\r\n([^\r\n]+)/)?.[1] ?? "";
      const upload: VoiceUpload = {
        contentType: request.headers()["content-type"] ?? "",
        bodyBytes: request.postDataBuffer()?.length ?? 0,
        expectedDoctorId,
        clientMessageId,
      };
      voiceUploads.push(upload);
      const message = syntheticVoiceMessage(upload, messages.length + 1);
      messages.push(message);
      await json(route, 201, message);
      for (const socket of sockets) {
        socket.send(
          JSON.stringify({
            type: "message.created",
            data: { patient_id: PATIENT_ID, doctor_id: DOCTOR_ID, message },
          }),
        );
      }
      return;
    }

    if (
      url.pathname.startsWith(`/api/v1/patients/${PATIENT_ID}/messages/`) &&
      url.pathname.endsWith("/audio")
    ) {
      await route.fulfill({
        status: 200,
        contentType: "audio/webm",
        body: "synthetic audio",
      });
      return;
    }

    if (url.pathname === "/api/v1/messaging/unread") {
      await json(route, 200, []);
      return;
    }

    if (url.pathname === "/api/v1/me/doses" && method === "GET") {
      await json(route, 200, EMPTY_DAY_DOSES);
      return;
    }

    if (url.pathname.startsWith("/api/v1/assessments")) {
      await json(route, 200, { items: [], next_cursor: null });
      return;
    }

    await json(route, 200, {});
  });

  return {
    messages,
    sendAttempts,
    voiceUploads,
    typingCommands,
    patientMessageRequests,
    failNextAuthMe() {
      failNextSessionCheck = true;
    },
    failNextTextSend() {
      failNextSend = true;
    },
    holdNextSuccessfulTextSend() {
      holdNextSuccessfulSend = true;
      heldSendStarted = new Promise<void>((resolve) => {
        heldSendStartedResolve = resolve;
      });
      heldSendResponse = new Promise<void>((resolve) => {
        releaseHeldSendResolve = resolve;
      });
    },
    waitForHeldTextSend() {
      return heldSendStarted;
    },
    releaseHeldTextSend() {
      releaseHeldSendResolve();
    },
    expireSocket() {
      authenticated = false;
      for (const socket of sockets) {
        socket.close({ code: 4401, reason: "Сессия завершена" });
      }
    },
  };
}
