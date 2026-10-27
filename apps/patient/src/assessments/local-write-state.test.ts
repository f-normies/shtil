import { describe, expect, it } from "vitest";
import {
  beginLocalAssessmentWrite,
  finishLocalAssessmentWrite,
  localAssessmentWritePending,
} from "./local-write-state";

describe("local assessment writes", () => {
  it("stays pending until every concurrent IndexedDB write has finished", () => {
    beginLocalAssessmentWrite();
    beginLocalAssessmentWrite();

    expect(localAssessmentWritePending()).toBe(true);
    finishLocalAssessmentWrite();
    expect(localAssessmentWritePending()).toBe(true);
    finishLocalAssessmentWrite();
    expect(localAssessmentWritePending()).toBe(false);
  });
});
