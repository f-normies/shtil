import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import { decisionChoices, reasonOf } from "../../card/alerts";
import { deputyNote } from "../../card/escalation";
import { formatDateTime } from "../../card/format";
import { refreshAlerts, useAlertDecisions, useAlerts, usePatient } from "../../card/queries";
import { canSnooze, routingSignals } from "../../card/referral";
import { signalView } from "../../card/signals";
import { alertBasis } from "../../card/sources";
import { LEVEL_ICON } from "../card/LevelBadge";
import { ActionSteps } from "../details/ActionSteps";
import { PatientHeader } from "../details/PatientHeader";
import { PatientTitle } from "../details/PatientTitle";
import { SourceLine } from "../source/SourceLine";
import { DecisionForm } from "./DecisionForm";
import { SignalReason } from "./ReasonBlock";
import { ReferralSheet } from "./ReferralSheet";
import { Section } from "./Section";
import { SnoozeButton } from "./SnoozeButton";
import { Timeline } from "./Timeline";

type AlertOut = Schemas["AlertOut"];

const LEVEL_TONE = { mandatory: "text-danger", desirable: "text-warning" } as const;

/**
 * Подробности сигнала (спека #95, раздел 5; кадры 03 и 03б): что требует решения, причина,
 * что сделать, решение и ход обработки. Решённый сигнал — без действий. Заместитель
 * (спека #56) берёт в работу и решает, но не направляет к психиатру и не откладывает.
 */
export function SignalDetails({
  alert,
  doctor,
}: {
  alert: AlertOut;
  doctor: { id: string; full_name: string };
}) {
  const queryClient = useQueryClient();
  const patient = usePatient(alert.patient_id);
  const decisions = useAlertDecisions();
  const acknowledge = $api.useMutation("post", "/api/v1/alerts/{alert_id}/acknowledge", {
    onSettled: () => refreshAlerts(queryClient),
  });
  const noContact = $api.useMutation("post", "/api/v1/alerts/{alert_id}/no-contact", {
    onSettled: () => refreshAlerts(queryClient),
  });
  const patientAlerts = useAlerts("open", alert.patient_id);
  // «Зафиксировать решение» из обзора и «Пациентов» ведёт сюда с `#decision`. Блок
  // появляется после загрузки сигнала, поэтому прокрутка и фокус — когда он смонтирован.
  const hash = useLocation({ select: (l) => l.hash });
  const focusDecision = useCallback(
    (el: HTMLDivElement | null) => {
      if (el && hash === "decision") {
        el.scrollIntoView({ block: "start" });
        el.focus({ preventScroll: true });
      }
    },
    [hash],
  );
  // Снимок сигналов маршрутизации на момент открытия листа: решение — только по ним (#35).
  const [referring, setReferring] = useState<AlertOut[] | null>(null);
  const routing = routingSignals(patientAlerts.data ?? [alert], alert.patient_id);
  // Пока сигналы пациента не загружены, снимок листа неполон: сервер ответил бы 409.
  const routingReady = patientAlerts.isSuccess;
  const view = signalView(alert);
  const basis = alertBasis(alert);
  const Icon = LEVEL_ICON[alert.level];
  const resolved = alert.state === "resolved";
  const options = decisionChoices(alert, decisions.data);
  const path = { params: { path: { alert_id: alert.id } } };
  const card = (
    <Link
      to="/patients/$patientId"
      params={{ patientId: alert.patient_id }}
      className="ui-button"
      data-variant="secondary"
    >
      Открыть карточку
    </Link>
  );

  return (
    <article className="flex min-w-0 max-w-[656px] flex-1 flex-col gap-5 px-8 py-6">
      {patient.data ? (
        <PatientTitle
          patient={patient.data}
          large
          doctorId={alert.as_deputy ? undefined : doctor.id}
          actions={card}
        />
      ) : (
        <PatientHeader name="Пациент" about="" large actions={card} />
      )}

      <Section
        title={
          resolved ? "Решён" : alert.level === "mandatory" ? "Требует решения" : "Требует внимания"
        }
      >
        <div className="flex flex-col gap-3">
          <p className="flex items-center justify-between">
            <span className={`flex items-center gap-2 font-semibold ${LEVEL_TONE[alert.level]}`}>
              <Icon aria-hidden className="size-5" />
              {view.levelLabel}
              {view.kindLabel && (
                <span className="font-normal text-ink-muted">· {view.kindLabel}</span>
              )}
            </span>
            <span className="text-sm text-ink-muted">
              {formatDateTime(alert.last_triggered_at)}
            </span>
          </p>
          <h2 className="text-xl font-semibold">{alert.title}</h2>
          {alert.as_deputy && <p className="text-sm font-semibold text-ink">{deputyNote(alert)}</p>}
          {view.detail && <p className="text-ink-muted">{view.detail}</p>}
          {basis && <SourceLine basis={basis} />}
          {!resolved && (
            <div className="flex flex-wrap gap-2">
              {alert.kind === "routing" && !alert.as_deputy && (
                <Button disabled={!routingReady} onClick={() => setReferring(routing)}>
                  Направить к психиатру
                </Button>
              )}
              {alert.state !== "in_work" && (
                <Button
                  variant={alert.kind === "routing" ? "secondary" : "primary"}
                  disabled={acknowledge.isPending}
                  onClick={() => acknowledge.mutate(path)}
                >
                  Взять в работу
                </Button>
              )}
              {alert.as_deputy
                ? null
                : alert.kind === "routing"
                  ? routingReady &&
                    canSnooze(routing) &&
                    decisions.data?.snooze && (
                      // Маршрутизация откладывается целиком: все сигналы пациента разом (#35).
                      <SnoozeButton
                        target={{ patientId: alert.patient_id, alertIds: routing.map((a) => a.id) }}
                        snooze={decisions.data.snooze}
                      />
                    )
                  : alert.level === "desirable" &&
                    decisions.data?.snooze && (
                      <SnoozeButton target={{ alertId: alert.id }} snooze={decisions.data.snooze} />
                    )}
              {alert.kind === "alert" && decisions.data?.no_contact && (
                <Button
                  variant="secondary"
                  disabled={noContact.isPending}
                  onClick={() => noContact.mutate(path)}
                >
                  {decisions.data.no_contact}
                </Button>
              )}
            </div>
          )}
          {(acknowledge.isError || noContact.isError) && (
            <Alert tone="error">Не удалось сохранить действие. Попробуйте ещё раз.</Alert>
          )}
        </div>
      </Section>

      {(reasonOf(alert) || view.quotes.length > 0) && (
        <Section title="Причина">
          <SignalReason alert={alert} />
        </Section>
      )}

      <Section title="Что сделать">
        <ActionSteps actions={alert.doctor_actions} />
      </Section>

      {!resolved && options.length > 0 && (
        <div ref={focusDecision} tabIndex={-1} className="outline-none">
          <Section
            id="decision"
            title="Зафиксировать решение"
            note="Решение попадёт в карточку пациента и в ход обработки."
          >
            <DecisionForm key={alert.id} alert={alert} options={options} />
          </Section>
        </div>
      )}

      {referring && (
        <ReferralSheet
          patientId={alert.patient_id}
          seen={referring}
          onClose={() => setReferring(null)}
        />
      )}

      <Section title="Ход обработки">
        <Timeline alert={alert} names={{ ...alert.actors, [doctor.id]: doctor.full_name }} />
      </Section>
    </article>
  );
}
