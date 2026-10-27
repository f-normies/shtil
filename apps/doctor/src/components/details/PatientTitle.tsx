import { $api, type Schemas } from "@compass/api-client";
import { Button, MessagingChat } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "@tanstack/react-router";
import { MessageSquare, Pencil, Phone } from "lucide-react";
import { type ReactNode, useState } from "react";
import { aboutLine } from "../../card/about";
import { detailOf } from "../../card/errors";
import { formatPhone } from "../../card/phone";
import { useDiagnoses, usePrescriptions, useUnread } from "../../card/queries";
import { todayIso } from "../../card/therapy";
import { useRealtimeState } from "../../card/useRealtime";
import { useInspector } from "../inspector/context";
import { patientCardScope } from "../inspector/focus";
import { PatientHeader } from "./PatientHeader";

type Patient = Schemas["PatientSummary"];

/**
 * Шапка пациента в подробностях, в тулбаре карточки и на экранах сигнала и предложения
 * (спека #13, раздел 10.3; кадры 03 и 05): ФИО своей строкой, ниже сведения через запятую
 * и действия — «Позвонить», правка номера, «Написать», затем `actions` экрана. Номер —
 * подсказка у кнопки; без номера — «Добавить телефон» (раздел 5.1). Заместитель (спека #56)
 * только звонит: правка профиля и чат ему закрыты.
 */
export function PatientTitle({
  patient,
  large = false,
  doctorId,
  actions,
}: {
  patient: Patient;
  large?: boolean;
  doctorId?: string;
  actions?: ReactNode;
}) {
  const diagnoses = useDiagnoses(patient.id);
  const prescriptions = usePrescriptions(patient.id);
  const [editing, setEditing] = useState(false);
  const lead = patient.access !== "deputy";
  const about = aboutLine({
    patient,
    diagnoses: diagnoses.data ?? [],
    prescriptions: prescriptions.data ?? [],
    today: todayIso(),
  });

  return (
    <div className="flex flex-col gap-3">
      <PatientHeader
        name={patient.full_name}
        about={about}
        large={large}
        actions={
          <>
            {patient.phone ? (
              <>
                <a
                  href={`tel:${patient.phone}`}
                  title={formatPhone(patient.phone)}
                  aria-label={`Позвонить, ${formatPhone(patient.phone)}`}
                  className="ui-button"
                  data-variant="secondary"
                >
                  <Phone aria-hidden className="size-4" />
                  Позвонить
                </a>
                {lead && (
                  <Button
                    variant="quiet"
                    aria-label="Изменить телефон"
                    title="Изменить телефон"
                    onClick={() => setEditing(true)}
                  >
                    <Pencil aria-hidden className="size-4" />
                  </Button>
                )}
              </>
            ) : (
              lead && (
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Добавить телефон
                </Button>
              )
            )}
            {lead && doctorId && <MessagingButton patient={patient} doctorId={doctorId} />}
            {actions}
          </>
        }
      />
      {editing && (
        // key: форма начинается заново у каждого пациента, номер одного не сохранится другому.
        <PhoneForm key={patient.id} patient={patient} onDone={() => setEditing(false)} />
      )}
    </div>
  );
}

function MessagingButton({ patient, doctorId }: { patient: Patient; doctorId: string }) {
  const inspector = useInspector();
  const realtime = useRealtimeState();
  const unread = useUnread();
  const pathname = useLocation({ select: (location) => location.pathname });
  const count = (unread.data ?? [])
    .filter((item) => item.patient_id === patient.id && item.doctor_id === doctorId)
    .reduce((total, item) => total + item.unread_count, 0);
  const chatProps = {
    ownerId: doctorId,
    role: "doctor" as const,
    patientId: patient.id,
    doctorId,
    sendTyping: realtime.sendTyping,
  };

  return (
    <Button
      variant="secondary"
      icon={<MessageSquare className="size-4" />}
      count={count > 0 ? count : undefined}
      aria-label={count > 0 ? `Написать, непрочитанных: ${count}` : "Написать"}
      onClick={(event) =>
        inspector.open(
          {
            id: `messaging:${patient.id}`,
            title: "Сообщения",
            withinScope: patientCardScope(pathname, patient.id),
            body: <MessagingChat {...chatProps} />,
          },
          event.currentTarget,
        )
      }
    >
      Написать
    </Button>
  );
}

function PhoneForm({ patient, onDone }: { patient: Patient; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState(patient.phone ?? "");
  const save = $api.useMutation("put", "/api/v1/patients/{patient_id}/phone", {
    onSuccess: async () => {
      onDone();
      // Только список и карточка: сигналы, предложения и шкалы от телефона не зависят.
      await queryClient.invalidateQueries({
        predicate: (q) =>
          q.queryKey[1] === "/api/v1/patients" || q.queryKey[1] === "/api/v1/patients/{patient_id}",
      });
    },
  });
  // 422 — номер не прошёл проверку формата; прочие ошибки — не про формат.
  const formatError = save.isError && Array.isArray(detailOf(save.error));

  return (
    <form
      className="flex flex-wrap items-start gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({
          params: { path: { patient_id: patient.id } },
          body: { phone: value.trim() || null },
        });
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        Телефон
        <input
          className="min-h-10 rounded-lg border border-line bg-surface px-3"
          type="tel"
          inputMode="tel"
          placeholder="+7 900 000-00-00"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-invalid={formatError}
          aria-describedby={save.isError ? "phone-error" : undefined}
        />
        {save.isError && (
          <span id="phone-error" className="text-danger">
            {formatError
              ? "Номер в формате +7 900 000-00-00"
              : "Не удалось сохранить номер. Попробуйте ещё раз."}
          </span>
        )}
      </label>
      <div className="flex gap-2 pt-6">
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
        <Button type="button" variant="secondary" onClick={onDone}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
