import type { Schemas } from "@compass/api-client";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import {
  PatientPreferencesError,
  patientPrescriptionsQueryKey,
  savePrescriptionAppearance,
} from "./api";
import { type PillDraft, pillDraftChanged, pillDraftReady } from "./pill-appearance";

type Item = Schemas["ScheduleItem"];

export type PillSaveState =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "saved" }
  | { status: "error"; conflict: boolean };

/**
 * Сохранение вида таблеток по назначениям: состояние каждого сохранения — по id назначения.
 * Онбординг зовёт `saveAll` по кнопке «Дальше», профиль — по «Сохранить вид».
 */
export function usePillAppearanceSaver(ownerId: string) {
  const queryClient = useQueryClient();
  const [states, setStates] = useState<Record<string, PillSaveState>>({});

  const save = useCallback(
    async (item: Item, appearance: Schemas["PillAppearanceChoice"] | null): Promise<boolean> => {
      const id = item.prescription_id;
      setStates((current) => ({ ...current, [id]: { status: "pending" } }));
      try {
        const updated = await savePrescriptionAppearance(ownerId, item, appearance);
        queryClient.setQueryData<Item[]>(patientPrescriptionsQueryKey(ownerId), (current) =>
          current?.map((prescription) =>
            prescription.prescription_id === updated.prescription_id ? updated : prescription,
          ),
        );
        setStates((current) => ({ ...current, [id]: { status: "saved" } }));
        return true;
      } catch (error) {
        const conflict = error instanceof PatientPreferencesError && error.status === 409;
        setStates((current) => ({ ...current, [id]: { status: "error", conflict } }));
        return false;
      }
    },
    [ownerId, queryClient],
  );

  const saveAll = useCallback(
    async (items: Item[], drafts: Record<string, PillDraft>): Promise<boolean> => {
      const results = await Promise.all(
        items.flatMap((item) => {
          const draft = drafts[item.prescription_id];
          if (!draft || !pillDraftReady(draft) || !pillDraftChanged(item, draft)) return [];
          return [save(item, draft)];
        }),
      );
      return results.every(Boolean);
    },
    [save],
  );

  const values = Object.values(states);
  return {
    states,
    save,
    saveAll,
    pending: values.some((state) => state.status === "pending"),
    failed: values.some((state) => state.status === "error"),
  };
}
