import type { Schemas } from "@compass/api-client";
import { expect, it } from "vitest";
import { latestDoctorMessage } from "./latest-doctor-message";

const PATIENT = "00000000-0000-4000-8000-0000000000a1";
const DOCTOR = "00000000-0000-4000-8000-0000000000d1";

function message(seq: number, senderId: string, text: string): Schemas["MessageOut"] {
  return {
    id: `m-${seq}`,
    seq,
    sender_id: senderId,
    client_message_id: `c-${seq}`,
    kind: "text",
    created_at: `2026-10-27T10:0${seq}:00.000Z`,
    text,
    transcription_status: null,
    transcription_text: null,
    duration_ms: null,
    transcription_error_code: null,
    analysis_status: null,
    analysis_skip_reason: null,
  };
}

it("skips the patient's own replies and returns the newest doctor message", () => {
  const page = [
    message(3, PATIENT, "Поняла, спасибо!"),
    message(2, DOCTOR, "Лучше отказаться от вина."),
    message(1, PATIENT, "Можно ли вино?"),
  ];
  expect(latestDoctorMessage(page, PATIENT)?.text).toBe("Лучше отказаться от вина.");
});

it("returns null when the page holds only the patient's messages", () => {
  expect(latestDoctorMessage([message(1, PATIENT, "Здравствуйте")], PATIENT)).toBeNull();
  expect(latestDoctorMessage([], PATIENT)).toBeNull();
});
