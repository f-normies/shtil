import { describe, expect, test, vi } from "vitest";
import type { AssessmentSyncOutcome } from "../assessments/sync";
import type { OutboxState } from "../assessments/types";
import { submissionOutcome } from "./submission-outcome";

const OWN = "own-submission";

function synced(confirmed: string[], alertRaised: string[] = []): AssessmentSyncOutcome {
  return {
    status: "complete",
    sessionGeneration: 1,
    submitted: confirmed.length,
    blocked: 0,
    confirmedSubmissionIds: confirmed,
    alertRaisedSubmissionIds: alertRaised,
  };
}

function deps(results: (AssessmentSyncOutcome | null)[], state: OutboxState | null) {
  return {
    syncNow: vi.fn(async () => results.shift() ?? null),
    outboxState: vi.fn(async () => state),
  };
}

// Спека #57, раздел 5.1: фраза о враче — только по исходу своей отправки.
describe("own submission outcome", () => {
  test("a confirmed answer with an alert is reported to the doctor", async () => {
    const d = deps([synced([OWN], [OWN])], null);

    expect(await submissionOutcome(OWN, true, d)).toBe("alert-raised");
  });

  test("a confirmed answer without an alert has no doctor notice", async () => {
    const d = deps([synced([OWN])], null);

    expect(await submissionOutcome(OWN, true, d)).toBe("confirmed");
  });

  test("a sync already in flight is followed by a second pass", async () => {
    // Один поток синхронизации: первый вызов вернул чужой проход без своей отправки.
    const d = deps([synced(["other"]), synced([OWN], [OWN])], null);

    expect(await submissionOutcome(OWN, true, d)).toBe("alert-raised");
    expect(d.syncNow).toHaveBeenCalledTimes(2);
  });

  test.each([
    ["pending answer waits for the network", "pending" as const, "queued"],
    ["blocked answer was rejected by the server", "blocked" as const, "rejected"],
    ["missing answer is not promised", null, "rejected"],
  ])("after two passes a %s", async (_name, state, expected) => {
    const d = deps([synced([]), synced([])], state);

    expect(await submissionOutcome(OWN, true, d)).toBe(expected);
    expect(d.syncNow).toHaveBeenCalledTimes(2);
  });

  test("without the help screen a sync in flight is not repeated", async () => {
    // Второй проход нужен только фразе о враче: без экрана форма ведёт себя как раньше.
    const d = deps([synced(["other"]), synced([OWN], [OWN])], "pending");

    expect(await submissionOutcome(OWN, true, d, { passes: 1 })).toBe("queued");
    expect(d.syncNow).toHaveBeenCalledTimes(1);
  });

  test("a failed network pass is not repeated", async () => {
    const d = deps([{ ...synced([]), status: "waiting" }], "pending");

    expect(await submissionOutcome(OWN, true, d)).toBe("queued");
    expect(d.syncNow).toHaveBeenCalledTimes(1);
  });

  test("offline the answer stays queued without a sync", async () => {
    const d = deps([], "pending");

    expect(await submissionOutcome(OWN, false, d)).toBe("queued");
    expect(d.syncNow).not.toHaveBeenCalled();
  });
});
