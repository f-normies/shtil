import createFetchClient from "openapi-fetch";
import { describe, expect, it } from "vitest";
import type { components, paths } from "../schema";
import * as messaging from "./index";

type Schemas = components["schemas"];

const pair = {
  ownerId: "patient-1",
  role: "patient" as const,
  patientId: "patient-1",
  expectedDoctorId: "doctor-1",
};

function message(overrides: Partial<Schemas["MessageOut"]> = {}): Schemas["MessageOut"] {
  return {
    analysis_skip_reason: null,
    analysis_status: null,
    client_message_id: "client-1",
    created_at: "2026-10-25T10:00:00Z",
    duration_ms: null,
    id: "message-1",
    kind: "text",
    sender_id: "patient-1",
    seq: 1,
    text: "Привет",
    transcription_error_code: null,
    transcription_status: null,
    transcription_text: null,
    ...overrides,
  };
}

describe("messaging API helpers", () => {
  it("merges history windows by message identity and sorts by sequence", () => {
    expect(messaging.mergeMessagePages).toBeTypeOf("function");
    if (typeof messaging.mergeMessagePages !== "function") return;

    const recent = message({ id: "m2", client_message_id: "c2", seq: 2, text: "Новый" });
    const older = message({ id: "m1", client_message_id: "c1", seq: 1, text: "Старый" });
    const duplicate = message({ id: "m2", client_message_id: "c2", seq: 2, text: "Обновлённый" });

    expect(messaging.mergeMessagePages([[recent], [older, duplicate]])).toEqual([older, recent]);
  });

  it("keeps history query keys scoped to owner and conversation", () => {
    expect(messaging.messagingKeys).toBeTypeOf("object");
    if (typeof messaging.messagingKeys !== "object") return;

    const first = messaging.messagingKeys.historyPage(pair, { beforeSeq: 50 });
    const otherOwner = messaging.messagingKeys.historyPage(
      { ...pair, ownerId: "patient-2" },
      { beforeSeq: 50 },
    );
    const otherPair = messaging.messagingKeys.historyPage(
      { ...pair, expectedDoctorId: "doctor-2" },
      { beforeSeq: 50 },
    );
    expect(first).not.toEqual(otherOwner);
    expect(first).not.toEqual(otherPair);
  });

  it("sends text and voice using the generated REST contract", async () => {
    expect(messaging.createMessagingApi).toBeTypeOf("function");
    if (typeof messaging.createMessagingApi !== "function") return;

    const requests: Request[] = [];
    const serverMessage = message();
    const client = createFetchClient<paths>({
      baseUrl: "https://compass.test",
      fetch: async (request) => {
        requests.push(request);
        return new Response(JSON.stringify(serverMessage), {
          status: 201,
          headers: { "content-type": "application/json" },
        });
      },
    });
    const api = messaging.createMessagingApi(client);

    await api.sendText(pair, { clientMessageId: "client-1", text: "Привет" });
    const audio = new Blob(["audio"], { type: "audio/webm" });
    await api.sendVoice(pair, { clientMessageId: "client-2", blob: audio, fileName: "voice.webm" });

    expect(requests).toHaveLength(2);
    const [textRequest, voiceRequest] = requests;
    if (!textRequest || !voiceRequest) throw new Error("Expected text and voice requests.");
    expect(textRequest.url).toContain("/api/v1/patients/patient-1/messages");
    expect(await textRequest.json()).toEqual({
      client_message_id: "client-1",
      expected_doctor_id: "doctor-1",
      text: "Привет",
    });
    expect(voiceRequest.headers.get("content-type")).toContain("multipart/form-data; boundary=");
    const form = await voiceRequest.formData();
    expect(form.get("client_message_id")).toBe("client-2");
    expect(form.get("expected_doctor_id")).toBe("doctor-1");
    expect(form.get("file")).toBeInstanceOf(Blob);
  });

  it("does not cache unread summaries returned for another owner", async () => {
    expect(messaging.createMessagingApi).toBeTypeOf("function");
    if (typeof messaging.createMessagingApi !== "function") return;
    const client = createFetchClient<paths>({
      baseUrl: "https://compass.test",
      fetch: async () =>
        new Response(
          JSON.stringify([
            {
              doctor_id: "doctor-1",
              last_message_at: "2026-10-25T10:00:00Z",
              last_message_id: "message-1",
              patient_id: "patient-2",
              unread_count: 1,
            },
          ]),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    });

    await expect(
      messaging.createMessagingApi(client).getUnread({ ownerId: "patient-1", role: "patient" }),
    ).rejects.toMatchObject({ code: "messaging_owner_mismatch" });
  });

  it("encodes typing as the server envelope and rate-limits active updates", () => {
    expect(messaging.encodeTypingCommand).toBeTypeOf("function");
    expect(messaging.createTypingRateLimiter).toBeTypeOf("function");
    if (
      typeof messaging.encodeTypingCommand !== "function" ||
      typeof messaging.createTypingRateLimiter !== "function"
    )
      return;

    expect(JSON.parse(messaging.encodeTypingCommand(pair, true))).toEqual({
      type: "message.typing",
      data: { patient_id: "patient-1", expected_doctor_id: "doctor-1", active: true },
    });
    const allow = messaging.createTypingRateLimiter(2000);
    expect(allow(true, 0)).toBe(true);
    expect(allow(true, 1999)).toBe(false);
    expect(allow(false, 2000)).toBe(true);
    expect(allow(true, 2001)).toBe(true);
  });
});
