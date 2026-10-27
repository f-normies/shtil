import type { Schemas } from "@compass/api-client";

/** The newest message written by the doctor; the page comes newest first. */
export function latestDoctorMessage(
  items: readonly Schemas["MessageOut"][],
  patientId: string,
): Schemas["MessageOut"] | null {
  return items.find((message) => message.sender_id !== patientId) ?? null;
}
