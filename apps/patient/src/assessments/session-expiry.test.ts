import { afterEach, describe, expect, it, vi } from "vitest";
import { PATIENT_A } from "../../e2e/assessment-fixtures";
import {
  clearOfflineAssessmentAccessDenial,
  isOfflineAssessmentAccessDenied,
} from "./offline-access";
import { lockLocalAccessOnExpiry } from "./session-expiry";

const lockFailure = () => new DOMException("Synthetic IndexedDB lock failure", "UnknownError");

describe("local answers after the session ends", () => {
  afterEach(() => clearOfflineAssessmentAccessDenial());

  it("locks the patient of the expired session without reading the current owner", async () => {
    // Only the lock is available: reading the current pointer would fail this test.
    const store = { lockOwnerPointer: vi.fn(async () => {}) };

    await lockLocalAccessOnExpiry(async () => store, PATIENT_A);

    expect(store.lockOwnerPointer).toHaveBeenCalledExactlyOnceWith(PATIENT_A);
    expect(isOfflineAssessmentAccessDenied()).toBe(false);
  });

  it("denies offline access when the lock cannot be written", async () => {
    const store = {
      lockOwnerPointer: vi.fn(async () => {
        throw lockFailure();
      }),
    };

    await lockLocalAccessOnExpiry(async () => store, PATIENT_A);

    expect(isOfflineAssessmentAccessDenied()).toBe(true);
  });

  it("denies offline access when the local database cannot be opened", async () => {
    await lockLocalAccessOnExpiry(async () => {
      throw lockFailure();
    }, PATIENT_A);

    expect(isOfflineAssessmentAccessDenied()).toBe(true);
  });

  it("leaves local data alone when the page showed no patient", async () => {
    const openStore = vi.fn(async () => ({ lockOwnerPointer: vi.fn(async () => {}) }));

    await lockLocalAccessOnExpiry(openStore, null);

    expect(openStore).not.toHaveBeenCalled();
    expect(isOfflineAssessmentAccessDenied()).toBe(false);
  });
});
