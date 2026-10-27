import { createContext, useContext } from "react";
import type { PatientSyncState } from "./diary/synchronization";

const PatientSyncContext = createContext<PatientSyncState | null>(null);

export const PatientSyncProvider = PatientSyncContext.Provider;

/** Read the sync controls exposed by the authenticated patient route shell. */
export function usePatientSyncState(): PatientSyncState {
  const value = useContext(PatientSyncContext);
  if (!value) throw new Error("Patient sync state is unavailable outside the route shell");
  return value;
}
