import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button, StatusIcon } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { hasNewFiring, reasonOf, stateLine } from "../../card/alerts";
import { evidenceText } from "../../card/evidence";
import { formatDateTime } from "../../card/format";
import { refreshAlerts, useScaleDefinitions } from "../../card/queries";
import { signalView } from "../../card/signals";
import { alertBasis } from "../../card/sources";
import { SourceLine } from "../source/SourceLine";
import { ActionSteps } from "./ActionSteps";

type AlertOut = Schemas["AlertOut"];

const TONE = { mandatory: "text-required", desirable: "text-advised" } as const;
const STATUS = { mandatory: "required", desirable: "advised" } as const;

/**
 * «Группа сигнала» (спека #13, раздел 10.3; компонент «Врач v4/Группа сигнала»): уровень
 * значком, словом и цветом, причина, данные с датой, что сделать, источник правила и
 * действия. Новый сигнал берут в работу здесь, решение фиксируют на экране «Сигналы».
 * Действия из `doctor_actions` — нумерованным списком, как на экране «Сигналы»: инструменты
 * к ним ведут через экран сигнала.
 */
export function SignalGroup({ alert }: { alert: AlertOut }) {
  const view = signalView(alert);
  const basis = alertBasis(alert);
  const queryClient = useQueryClient();
  const acknowledge = $api.useMutation("post", "/api/v1/alerts/{alert_id}/acknowledge", {
    onSettled: () => refreshAlerts(queryClient),
  });
  const now = new Date();
  const reason = reasonOf(alert);
  const definition = useScaleDefinitions(reason ? [reason.scale] : []).get(reason?.scale ?? "");
  const data = [reason && evidenceText(reason, definition, view.firedAt), view.detail].filter(
    Boolean,
  );

  return (
    <article className="flex flex-col gap-3 rounded-xl bg-surface p-4">
      <header className="flex items-center justify-between gap-3 text-sm">
        <span className={`flex items-center gap-2 text-[15px] font-semibold ${TONE[alert.level]}`}>
          <StatusIcon status={STATUS[alert.level]} decorative />
          {view.levelLabel}
          {view.kindLabel && <span className="font-normal text-ink-muted">· {view.kindLabel}</span>}
        </span>
        <span className="text-ink-muted">{formatDateTime(alert.last_triggered_at, now)}</span>
      </header>
      <h3 className="text-xl leading-[26px] font-semibold text-ink">{alert.title}</h3>

      {data.length > 0 && <p className="text-[15px] text-ink-muted">{data.join(" ")}</p>}
      {view.quotes.map((q) => (
        <blockquote key={q.text} className="border-l-2 border-line-strong pl-3 text-ink">
          «{q.text}»
          {!q.found && (
            <span className="ml-2 text-xs text-ink-muted">(цитата не найдена в тексте)</span>
          )}
        </blockquote>
      ))}
      {view.quotes.length > 0 && (
        <p className="text-xs text-ink-muted">
          {[view.originLabel, view.recordLabel].filter(Boolean).join(", ")}
        </p>
      )}
      {alert.doctor_actions.length > 0 && (
        <div className="text-[15px] text-ink">
          <ActionSteps actions={alert.doctor_actions} />
        </div>
      )}
      {alert.state === "in_work" && (
        // Сигнал в работе ждёт решения; новое срабатывание «Обязательно» видно словами.
        <p
          className={`text-sm ${hasNewFiring(alert) ? "font-semibold text-ink" : "text-ink-muted"}`}
        >
          {stateLine(alert, now)}
          {hasNewFiring(alert) && ": зафиксируйте решение"}
        </p>
      )}
      {alert.occurrences > 1 && (
        <p className="text-sm text-ink-muted">Срабатываний: {alert.occurrences}</p>
      )}

      {basis && <SourceLine basis={basis} />}

      {acknowledge.isError && <Alert tone="error">Не удалось взять сигнал в работу.</Alert>}
      <div className="flex flex-wrap gap-2">
        {alert.state === "new" ? (
          <Button
            disabled={acknowledge.isPending}
            onClick={() => acknowledge.mutate({ params: { path: { alert_id: alert.id } } })}
          >
            Взять в работу
          </Button>
        ) : (
          <Link
            to="/alerts/$alertId"
            params={{ alertId: alert.id }}
            search={{}}
            hash="decision"
            className="ui-button"
            data-variant="primary"
          >
            Зафиксировать решение
          </Link>
        )}
        <Link
          to="/alerts/$alertId"
          params={{ alertId: alert.id }}
          search={{}}
          className="ui-button"
          data-variant="secondary"
        >
          Открыть сигнал
        </Link>
      </div>
    </article>
  );
}
