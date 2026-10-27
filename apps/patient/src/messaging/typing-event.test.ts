import { messagingKeys } from "@compass/api-client";
import { applyTypingEvent } from "@compass/ui";
import { QueryClient } from "@tanstack/react-query";
import { expect, it } from "vitest";

const PATIENT = "00000000-0000-4000-8000-0000000000a1";
const DOCTOR = "00000000-0000-4000-8000-0000000000d1";

function status(typingUntil: string | null) {
  return {
    doctor_id: DOCTOR,
    patient_id: PATIENT,
    peer_full_name: "Иванова",
    online: true,
    last_seen_at: null,
    typing_until: typingUntil,
  };
}

it("writes the peer's typing lease into the doctor's status query without a refetch", () => {
  const queryClient = new QueryClient();
  const owner = { ownerId: DOCTOR, role: "doctor" as const };
  const key = messagingKeys.status({ ...owner, patientId: PATIENT, expectedDoctorId: DOCTOR });
  queryClient.setQueryData(key, status(null));
  const applied = applyTypingEvent(queryClient, owner, {
    patient_id: PATIENT,
    doctor_id: DOCTOR,
    user_id: PATIENT,
    typing_until: "2026-10-27T10:00:05.000Z",
  });
  expect(applied).toBe(true);
  expect(queryClient.getQueryData(key)).toMatchObject({ typing_until: "2026-10-27T10:00:05.000Z" });
});

it("writes into the patient's bootstrap status and ignores the owner's own typing", () => {
  const queryClient = new QueryClient();
  const owner = { ownerId: PATIENT, role: "patient" as const };
  const key = [...messagingKeys.owner(owner), "bootstrap-status", PATIENT];
  queryClient.setQueryData(key, status(null));
  expect(
    applyTypingEvent(queryClient, owner, {
      patient_id: PATIENT,
      doctor_id: DOCTOR,
      user_id: PATIENT,
      typing_until: "2026-10-27T10:00:05.000Z",
    }),
  ).toBe(false);
  expect(queryClient.getQueryData(key)).toMatchObject({ typing_until: null });
  expect(
    applyTypingEvent(queryClient, owner, {
      patient_id: PATIENT,
      doctor_id: DOCTOR,
      user_id: DOCTOR,
      typing_until: null,
    }),
  ).toBe(true);
  expect(queryClient.getQueryData(key)).toMatchObject({ typing_until: null });
});
