import type { Schemas } from "@compass/api-client";
import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import type { PatientSession } from "../session";
import {
  fetchAcceptedConsent,
  fetchPatientPrescriptions,
  patientConsentQueryKey,
  patientPreferencesQueryKey,
  patientPrescriptionsQueryKey,
  readPatientPreferences,
} from "./api";
import { readCachedPatientPreferences } from "./cache";
import { moodAttribute } from "./onboarding-flow";

export type AccessiblePatientSession = Extract<PatientSession, { status: "online" | "offline" }>;

export function patientOwnerId(session: AccessiblePatientSession): string {
  return session.status === "online" ? session.user.id : session.ownerId;
}

export function usePatientPreferences(
  session: PatientSession,
): UseQueryResult<Schemas["PatientPreferencesOut"], Error> {
  const accessible = session.status === "online" || session.status === "offline";
  const ownerId = accessible ? patientOwnerId(session) : "unavailable";
  return useQuery({
    queryKey: patientPreferencesQueryKey(ownerId),
    enabled: accessible,
    queryFn: () => {
      if (!accessible) throw new Error("Настройки профиля недоступны.");
      return readPatientPreferences(ownerId, session.status === "online");
    },
    staleTime: 30_000,
  });
}

export function usePatientPrescriptions(
  session: AccessiblePatientSession,
  enabled = true,
): UseQueryResult<Schemas["ScheduleItem"][], Error> {
  const ownerId = patientOwnerId(session);
  return useQuery({
    queryKey: patientPrescriptionsQueryKey(ownerId),
    enabled,
    queryFn: async () => {
      if (session.status === "online") return fetchPatientPrescriptions(ownerId);
      const cached = await readCachedPatientPreferences(ownerId);
      if (!cached?.prescriptions) throw new Error("Схема приёма недоступна без подключения.");
      return cached.prescriptions;
    },
    staleTime: 30_000,
  });
}

export function useAcceptedConsent(
  session: AccessiblePatientSession,
  enabled = true,
): UseQueryResult<Schemas["AcceptedConsentOut"], Error> {
  const ownerId = patientOwnerId(session);
  return useQuery({
    queryKey: patientConsentQueryKey(ownerId),
    enabled,
    queryFn: async () => {
      if (session.status === "online") return fetchAcceptedConsent(ownerId);
      const cached = await readCachedPatientPreferences(ownerId);
      if (!cached?.consent) throw new Error("Согласие недоступно без подключения.");
      return cached.consent;
    },
    staleTime: 30_000,
  });
}

export function useApplyPatientPreferences(
  preferences: Schemas["PatientPreferencesOut"] | undefined,
): void {
  useEffect(() => {
    if (!preferences) return;
    const root = document.documentElement;
    root.dataset.nastroenie = moodAttribute(preferences.mood_palette);
    root.dataset.textScale = preferences.text_size;
    return () => {
      delete root.dataset.nastroenie;
      delete root.dataset.textScale;
    };
  }, [preferences?.mood_palette, preferences?.text_size, preferences]);
}
