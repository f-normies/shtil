import { useSyncExternalStore } from "react";
import { localAssessmentWritePending, subscribeToLocalAssessmentWrites } from "./local-write-state";

/** Read whether any local assessment transaction is still running. */
export function useLocalAssessmentWritePending(): boolean {
  return useSyncExternalStore(
    subscribeToLocalAssessmentWrites,
    localAssessmentWritePending,
    () => false,
  );
}
