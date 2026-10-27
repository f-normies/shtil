import { afterEach, expect, it, vi } from "vitest";
import {
  clearOfflineAssessmentAccessDenial,
  isOfflineAssessmentAccessDenied,
} from "../assessments/offline-access";
import type { AssessmentStore } from "../assessments/store";
import type { DiaryStore } from "./store";
import { createDiarySyncEngine } from "./sync";

const OWNER_ID = "00000000-0000-4000-8000-0000000000a1";

afterEach(() => clearOfflineAssessmentAccessDenial());

it("denies offline access when the session is expired and owner lock fails", async () => {
  const lockOwnerPointer = vi.fn(async () => {
    throw new DOMException("Synthetic IndexedDB lock failure", "UnknownError");
  });
  const assessments = {
    getOwnerPointer: vi.fn(async () => ({ id: OWNER_ID, role: "patient" as const, locked: false })),
    lockOwnerPointer,
  } as unknown as AssessmentStore;
  const engine = createDiarySyncEngine({} as DiaryStore, assessments, {
    getCurrentUser: async () => null,
    getSessionGeneration: () => 7,
    createEntry: async () => ({ status: null }),
  });

  await expect(engine.sync(OWNER_ID, 7)).resolves.toMatchObject({
    status: "authentication-required",
  });
  expect(lockOwnerPointer).toHaveBeenCalledExactlyOnceWith(OWNER_ID);
  expect(isOfflineAssessmentAccessDenied()).toBe(true);
});
