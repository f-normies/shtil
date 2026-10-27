import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { showsFailure } from "../../card/loadState";
import {
  useLatestAssessments,
  useOpenAlerts,
  usePatients,
  useProposalsOf,
  useUnread,
} from "../../card/queries";
import { patientQueue } from "../../card/queue";
import { ConsentBanner } from "../../components/details/ConsentBanner";
import { InvitePatient } from "../../components/InvitePatient";
import { PatientDetails } from "../../components/patients/PatientDetails";
import { PatientList } from "../../components/patients/PatientList";

type PatientsSearch = { patient?: string; invite?: true };

/**
 * «Пациенты» (спека #13, раздел 10.2; кадр «Врач v4 · 02»): список 360 px и подробности
 * выбранного пациента. Выбор — параметр `?patient=` (раздел 10.9): «‹ Пациенты» в карточке
 * возвращает к той же строке. Очередь кабинет собирает из существующих ответов API.
 */
export const Route = createFileRoute("/_doctor/")({
  validateSearch: (search: Record<string, unknown>): PatientsSearch => ({
    patient: typeof search.patient === "string" ? search.patient : undefined,
    invite: search.invite === true || search.invite === "true" ? true : undefined,
  }),
  component: PatientsScreen,
});

function PatientsScreen() {
  const search = Route.useSearch();
  const { user } = Route.useRouteContext();
  const navigate = useNavigate({ from: Route.fullPath });
  const patients = usePatients();
  const alerts = useOpenAlerts();
  const unread = useUnread();
  const ids = (patients.data ?? []).map((p) => p.id);
  const { proposals, pending } = useProposalsOf(ids);
  const latest = useLatestAssessments(ids);
  const { attention, rest } = patientQueue({
    patients: patients.data ?? [],
    alerts: alerts.data ?? [],
    proposals,
    unread: unread.data ?? [],
    latest,
    now: new Date(),
  });
  const rows = [...attention, ...rest];
  const selected = rows.find((r) => r.patient.id === search.patient);
  const inviting = search.invite || (patients.isSuccess && rows.length === 0);
  // Без выбора — первый в очереди, но только когда очередь собрана: иначе подробности
  // перескакивали бы с пациента на пациента по мере ответов. Выбор пишется в адрес,
  // и новый сигнал позже его не сдвигает.
  const settled = patients.isSuccess && alerts.isSuccess && unread.isFetched && !pending;
  const first = settled && !search.patient && !inviting ? rows[0]?.patient.id : undefined;
  useEffect(() => {
    if (first) {
      void navigate({ search: { patient: first }, replace: true });
    }
  }, [first, navigate]);

  // Адрес с чужим или несуществующим пациентом: очередь собрана, а строки нет (D1-16).
  const missing = settled && !inviting && Boolean(search.patient) && !selected;

  // Ошибка — только когда показать нечего: при обрыве сети очередь остаётся из кэша,
  // о задержке говорит строка «Нет связи с сервером» (кадр 12).
  if (showsFailure(alerts)) {
    return (
      <section role="alert" className="flex flex-col items-start gap-3 px-8 py-6">
        <h1 className="text-2xl font-semibold">Пациенты</h1>
        <p className="text-sm text-danger">
          Не удалось загрузить сигналы. Очередь пациентов недоступна.
        </p>
        <button
          type="button"
          className="h-9 text-sm font-semibold text-primary-text"
          onClick={() => void alerts.refetch()}
        >
          Повторить
        </button>
      </section>
    );
  }

  return (
    <div className="flex min-h-0 flex-1">
      <PatientList
        attention={alerts.isPending ? [] : attention}
        rest={alerts.isPending ? [] : rest}
        selectedId={inviting ? undefined : selected?.patient.id}
        onSelect={(id) => navigate({ search: { patient: id }, replace: true })}
        onOpen={(id) => navigate({ to: "/patients/$patientId", params: { patientId: id } })}
        onInvite={() => navigate({ search: { ...search, invite: true } })}
        loading={patients.isPending || alerts.isPending}
        failed={showsFailure(patients)}
        onRetry={() => void patients.refetch()}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {!inviting && selected && <ConsentBanner revokedAt={selected.patient.consent_revoked_at} />}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {inviting ? (
            <div className="flex max-w-[688px] flex-col gap-4 px-8 pt-6">
              {rows.length === 0 && <h2 className="text-2xl font-semibold">Пациентов пока нет</h2>}
              <InvitePatient />
            </div>
          ) : missing ? (
            <p className="px-8 pt-6 text-[15px] text-ink-muted">
              Пациент не найден. Выберите пациента в списке слева.
            </p>
          ) : (
            selected && (
              <PatientDetails
                key={selected.patient.id}
                patient={selected.patient}
                doctorId={user.id}
              />
            )
          )}
        </div>
      </div>
    </div>
  );
}
