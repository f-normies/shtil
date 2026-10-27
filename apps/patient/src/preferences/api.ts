import { fetchClient, type Schemas } from "@compass/api-client";
import type { QueryClient } from "@tanstack/react-query";
import { cachePatientPreferences, readCachedPatientPreferences } from "./cache";
import type { EditablePreferences } from "./onboarding-flow";

export const PATIENT_PREFERENCES_KEY = ["patient-preferences"] as const;
export const PATIENT_PRESCRIPTIONS_KEY = ["patient-prescriptions"] as const;
export const PATIENT_CONSENT_KEY = ["patient-consent"] as const;

export const patientPreferencesQueryKey = (ownerId: string) =>
  [...PATIENT_PREFERENCES_KEY, ownerId] as const;
export const patientPrescriptionsQueryKey = (ownerId: string) =>
  [...PATIENT_PRESCRIPTIONS_KEY, ownerId] as const;
export const patientConsentQueryKey = (ownerId: string) =>
  [...PATIENT_CONSENT_KEY, ownerId] as const;

export class PatientPreferencesError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "PatientPreferencesError";
  }
}

function requestError(status: number, data: unknown): PatientPreferencesError {
  const code =
    typeof data === "object" && data !== null && "code" in data && typeof data.code === "string"
      ? data.code
      : undefined;
  return new PatientPreferencesError(
    `Patient preferences request failed (${status})`,
    status,
    code,
  );
}

export async function fetchPatientPreferences(
  ownerId: string,
): Promise<Schemas["PatientPreferencesOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/me/preferences");
  if (!response.ok || !data) throw requestError(response.status, error);
  try {
    await cachePatientPreferences(ownerId, { preferences: data });
  } catch {
    // A storage quota or browser privacy setting must not hide a successful server response.
  }
  return data;
}

export async function readPatientPreferences(
  ownerId: string,
  online: boolean,
): Promise<Schemas["PatientPreferencesOut"]> {
  if (online) return fetchPatientPreferences(ownerId);
  const cached = await readCachedPatientPreferences(ownerId);
  if (!cached) throw new Error("Сохранённые настройки недоступны без подключения.");
  return cached.preferences;
}

export async function fetchPatientPrescriptions(
  ownerId: string,
): Promise<Schemas["ScheduleItem"][]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/me/prescriptions");
  if (!response.ok || !data) throw requestError(response.status, error);
  try {
    await cachePatientPreferences(ownerId, { prescriptions: data });
  } catch {
    // Profile rendering remains available when browser storage cannot be written.
  }
  return data;
}

export async function fetchAcceptedConsent(
  ownerId: string,
): Promise<Schemas["AcceptedConsentOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/me/consent");
  if (!response.ok || !data) throw requestError(response.status, error);
  try {
    await cachePatientPreferences(ownerId, { consent: data });
  } catch {
    // Consent is optional to the settings cache and can be fetched again online.
  }
  return data;
}

export async function savePatientPreferences(
  ownerId: string,
  expectedRevision: number,
  values: Partial<EditablePreferences>,
  complete = false,
): Promise<Schemas["PatientPreferencesOut"]> {
  const body: Schemas["PatientPreferencesUpdate"] = {
    expected_revision: expectedRevision,
    ...values,
  };
  const result = complete
    ? await fetchClient.POST("/api/v1/me/preferences/complete", { body })
    : await fetchClient.PATCH("/api/v1/me/preferences", { body });
  if (!result.response.ok || !result.data) {
    throw requestError(result.response.status, result.error);
  }
  try {
    await cachePatientPreferences(ownerId, { preferences: result.data });
  } catch {
    // The server remains the source of truth; cache failure is not a failed save.
  }
  return result.data;
}

export async function savePrescriptionAppearance(
  ownerId: string,
  item: Schemas["ScheduleItem"],
  appearance: Schemas["PillAppearanceChoice"] | null,
): Promise<Schemas["ScheduleItem"]> {
  const { data, error, response } = await fetchClient.PUT(
    "/api/v1/me/prescriptions/{prescription_id}/appearance",
    {
      params: { path: { prescription_id: item.prescription_id } },
      body: { expected_revision: item.appearance_revision, appearance },
    },
  );
  if (!response.ok || !data) throw requestError(response.status, error);
  const updated = {
    ...item,
    appearance: data.appearance,
    appearance_origin: data.appearance_origin,
    appearance_revision: data.appearance_revision,
    default_appearance: item.default_appearance,
  } satisfies Schemas["ScheduleItem"];
  const cached = await readCachedPatientPreferences(ownerId).catch(() => null);
  if (cached?.prescriptions) {
    const prescriptions = cached.prescriptions.map((prescription) =>
      prescription.prescription_id === updated.prescription_id ? updated : prescription,
    );
    try {
      await cachePatientPreferences(ownerId, { prescriptions });
    } catch {
      // Mutation success is already confirmed by the server.
    }
  }
  return updated;
}

export function invalidatePatientPreferences(queryClient: QueryClient, ownerId: string): void {
  void queryClient.invalidateQueries({ queryKey: patientPreferencesQueryKey(ownerId) });
}
