import type { OutboxError } from "./types";

export type SubmissionDecision =
  | { kind: "confirmed" }
  | { kind: "retry" }
  | { kind: "reauthenticate" }
  | { kind: "owner-mismatch" }
  | { kind: "blocked"; error: OutboxError };

/** Classify a submission response without changing the queued payload. */
export function classifySubmissionResponse(
  status: number | null,
  code?: string,
): SubmissionDecision {
  if (status === 200 || status === 201) return { kind: "confirmed" };
  if (status === null || status < 200 || status === 408 || status === 429 || status >= 500) {
    return { kind: "retry" };
  }
  if (status === 401) return { kind: "reauthenticate" };
  if (status === 403 && code === "patient_mismatch") return { kind: "owner-mismatch" };
  if (status >= 400 && status < 500) {
    return { kind: "blocked", error: { status, code: code ?? `http_${status}` } };
  }
  return { kind: "retry" };
}

const TEMPORARY_BLOCK_CODES = new Set(["scale_unavailable", "lead_doctor_missing"]);

/** Whether a blocked submission may be resent unchanged after the patient asks for it. */
export function canRetryBlockedSubmission(error: OutboxError | null): boolean {
  return error !== null && TEMPORARY_BLOCK_CODES.has(error.code);
}
