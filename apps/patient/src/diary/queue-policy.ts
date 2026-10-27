export type DiaryResponseDecision =
  | { kind: "confirmed" }
  | { kind: "retry" }
  | { kind: "reauthenticate" }
  | { kind: "owner-mismatch" }
  | { kind: "conflict" }
  | { kind: "blocked" };

/** Keep transient failures retryable and distinguish authentication and data conflicts. */
export function classifyDiaryResponse(
  status: number | null,
  _code?: string,
): DiaryResponseDecision {
  if (status === null || status >= 500 || status === 408 || status === 429) {
    return { kind: "retry" };
  }
  if (status >= 200 && status < 300) return { kind: "confirmed" };
  if (status === 401) return { kind: "reauthenticate" };
  if (status === 403) return { kind: "owner-mismatch" };
  if (status === 409) return { kind: "conflict" };
  return { kind: "blocked" };
}
