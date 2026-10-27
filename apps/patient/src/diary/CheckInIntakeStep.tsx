import type { Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import type { IntakeMark } from "../medications/doses";
import { IntakeInstructions, IntakeRow } from "../medications/IntakeRow";
import { usePatientSyncState } from "../patient-sync-context";
import { resolveDiaryClock } from "./clock";
import { getDiaryStore, loadIntakeView } from "./queries";
import type { DiaryContextCacheRecord, IntakeOutboxRecord, IntakeView } from "./types";

/** Отметки по дозам: серверные, поверх них — ещё не отправленные из очереди. */
export function intakeMarksByDose(
  marks: readonly Schemas["IntakeMarkOut"][] | undefined,
  outbox: readonly IntakeOutboxRecord[] | undefined,
  scheduledOn: string,
): Map<string, IntakeMark> {
  const byDose = new Map<string, IntakeMark>();
  for (const mark of marks ?? []) {
    byDose.set(`${mark.prescription_id}:${mark.dose_ordinal}`, {
      status: mark.status,
      occurred_at: mark.occurred_at,
    });
  }
  for (const command of outbox ?? []) {
    if (command.payload.scheduled_on !== scheduledOn) continue;
    byDose.set(`${command.payload.prescription_id}:${command.payload.dose_ordinal}`, {
      status: command.payload.status,
      occurred_at: command.payload.occurred_at,
    });
  }
  return byDose;
}

/** Prescription rows use only the actual scheme and explicit patient reports. */
export function CheckInIntakeStep({
  ownerId,
  prescriptions,
  cache,
  scheduledOn,
  online,
  otherMedicineText,
  onOtherMedicineText,
}: {
  ownerId: string;
  prescriptions: Schemas["ScheduleItem"][] | null;
  cache: DiaryContextCacheRecord | null;
  scheduledOn: string;
  online: boolean;
  otherMedicineText: string;
  onOtherMedicineText: (text: string) => void;
}) {
  const queryClient = useQueryClient();
  const { diarySyncNow } = usePatientSyncState();
  const marksQuery = useQuery({
    queryKey: ["diary", ownerId, "intake", scheduledOn],
    retry: false,
    networkMode: "always",
    queryFn: (): Promise<IntakeView> => loadIntakeView(ownerId, scheduledOn, online),
  });
  const outboxQuery = useQuery({
    queryKey: ["diary", ownerId, "intake-outbox"],
    retry: false,
    networkMode: "always",
    queryFn: async () => (await getDiaryStore()).getIntakeOutboxForOwner(ownerId),
  });
  const markByDose = intakeMarksByDose(marksQuery.data?.items, outboxQuery.data, scheduledOn);

  async function toggle(prescription: Schemas["ScheduleItem"], ordinal: number): Promise<void> {
    const key = `${prescription.prescription_id}:${ordinal}`;
    const status = markByDose.get(key)?.status === "taken" ? "unmarked" : "taken";
    try {
      await (await getDiaryStore()).enqueueIntakeMark({
        ownerId,
        payload: {
          operation_id: crypto.randomUUID(),
          prescription_id: prescription.prescription_id,
          scheduled_on: scheduledOn,
          dose_ordinal: ordinal,
          status,
          occurred_at: resolveDiaryClock(cache).occurredAt,
        },
      });
      await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake", scheduledOn] });
      await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake-outbox"] });
      void diarySyncNow();
    } catch {
      await queryClient.invalidateQueries({ queryKey: ["diary", ownerId, "intake-outbox"] });
    }
  }

  return (
    <div className="patient-checkin-main patient-checkin-main--intake">
      <h1 className="type-patient-question">Лекарства на сегодня</h1>
      <p className="type-patient-body patient-checkin-subtitle">
        Отметьте, что уже приняли. Если приём пропущен, оставьте пустым.
      </p>
      <section className="patient-intake-card" aria-label="Назначения и отметки приёма">
        {prescriptions === null ? (
          <p className="patient-intake-empty">
            Схема приёма недоступна. Вы можете продолжить без неё.
          </p>
        ) : prescriptions.length === 0 ? (
          <p className="patient-intake-empty">Действующих назначений на сегодня нет.</p>
        ) : (
          <>
            <div className="patient-intake-card__rows">
              {prescriptions.map((prescription) =>
                Array.from({ length: prescription.times_per_day }, (_, index) => {
                  const ordinal = index + 1;
                  const key = `${prescription.prescription_id}:${ordinal}`;
                  return (
                    <IntakeRow
                      key={key}
                      prescription={prescription}
                      ordinal={ordinal}
                      mark={markByDose.get(key)}
                      onToggle={() => void toggle(prescription, ordinal)}
                    />
                  );
                }),
              )}
            </div>
            <IntakeInstructions prescriptions={prescriptions} />
          </>
        )}
        <OtherMedicineField text={otherMedicineText} onChange={onOtherMedicineText} />
      </section>
      {cache?.prescriptions_fetched_at && prescriptions === null && (
        <p className="patient-data-note">Схема недоступна. Отметка дневника сохранится.</p>
      )}
    </div>
  );
}

/** «Другое лекарство» (кадр 16б): поле под строкой, «Добавить» сохраняет, «Отмена» сворачивает. */
function OtherMedicineField({
  text,
  onChange,
}: {
  text: string;
  onChange: (text: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const saved = text.trim();

  // Поле раскрылось по касанию — курсор сразу в нём, клавиатура поднимает экран (кадр 16б).
  useEffect(() => {
    if (editing) fieldRef.current?.focus();
  }, [editing]);

  if (editing) {
    return (
      <div className="patient-other-medication">
        <label className="patient-other-medication__label" htmlFor="other-medicine-text">
          Что ещё приняли и когда
        </label>
        <textarea
          id="other-medicine-text"
          maxLength={20_000}
          rows={4}
          value={draft}
          ref={fieldRef}
          onChange={(event) => setDraft(event.currentTarget.value)}
        />
        <p className="patient-other-medication__hint">Врач увидит запись вместе с отметкой.</p>
        <div className="patient-other-medication__actions">
          <Button
            variant="secondary"
            onClick={() => {
              setDraft(text);
              setEditing(false);
            }}
          >
            Отмена
          </Button>
          <Button
            disabled={!draft.trim()}
            onClick={() => {
              onChange(draft.trim());
              setEditing(false);
            }}
          >
            Добавить
          </Button>
        </div>
      </div>
    );
  }

  if (saved) {
    return (
      <div className="patient-other-medication__saved">
        <p>
          <small>Другое лекарство</small>
          {saved}
        </p>
        <Button
          variant="link"
          onClick={() => {
            setDraft(text);
            setEditing(true);
          }}
        >
          Изменить
        </Button>
      </div>
    );
  }

  return (
    <button
      className="patient-other-medication-toggle"
      type="button"
      onClick={() => {
        setDraft("");
        setEditing(true);
      }}
    >
      <span aria-hidden="true">+</span> Другое лекарство
    </button>
  );
}
