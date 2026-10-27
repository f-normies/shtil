import { $api, type Schemas } from "@compass/api-client";
import { Button, StatusIcon } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Clock, History, Info, type LucideIcon, PencilLine } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { refreshSummary } from "../../card/queries";
import {
  confirmError,
  confirmedText,
  retryError,
  type SummaryView,
  summaryView,
} from "../../card/summary";
import { storeSummary, useVisitSummary } from "../../card/visitSummary";
import { PROPOSAL_OUTLINE } from "../signals/Section";
import { StatementRow } from "./StatementRow";
import { SummarySheet } from "./SummarySheet";

type Summary = Schemas["VisitSummaryOut"];

const CONFIRM = "/api/v1/patients/{patient_id}/summary/{summary_id}/confirm";
const RETRY = "/api/v1/patients/{patient_id}/summary/retry";
const STROKE = 1.75;

/** Действие в пометке группы: «Повторить» или «Обновить». */
type NoticeAction = { busy: boolean; text: string | null; run: () => void };

/**
 * Группа «Сводка с прошлого приёма» в «Обзоре» (спека #38, раздел 3.6; кадры 05б и 05в).
 * Черновик — предложение системы в пунктире; подтверждённая — сплошная. Ошибка запроса
 * остаётся в группе: остальные группы «Обзора» работают. Опрос — не дольше 3 минут.
 */
export function SummaryGroup({
  patientId,
  currentDoctorId,
}: {
  patientId: string;
  currentDoctorId: string;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const { query, slow, refresh } = useVisitSummary(patientId);
  const [editing, setEditing] = useState<Summary | null>(null);
  const confirm = $api.useMutation("post", CONFIRM, {
    onSuccess: (data) => {
      storeSummary(queryClient, patientId, data);
      setEditing(null);
    },
    onError: (error) => {
      // 409: черновик устарел или сводку уже подтвердили — лист закрываем, сводку запрашиваем.
      if (confirmError(error).refetch) {
        setEditing(null);
        void refreshSummary(queryClient);
      }
    },
  });
  const retry = $api.useMutation("post", RETRY, {
    onSuccess: (data) => storeSummary(queryClient, patientId, data),
    onError: (error) => {
      // 409 `not_failed`: расчёт уже идёт или готов — показываем текущую сводку без ошибки.
      if (retryError(error).refetch) {
        void refreshSummary(queryClient);
      }
    },
  });
  const summary = query.data;
  const view = summary ? summaryView(summary, { slow }) : null;
  const failure = confirm.error ? confirmError(confirm.error) : null;
  // Сообщение об устаревшем черновике держим, пока не пришёл новый черновик.
  const failedId = confirm.variables?.params.path.summary_id;
  const fresh = view?.kind === "draft" && summary?.id !== failedId;
  const showFailure = Boolean(failure?.text) && !editing && !fresh;
  const send = (snapshot: Summary, body: Schemas["ConfirmSummaryIn"]) => {
    if (snapshot.id) {
      confirm.mutate({
        params: { path: { patient_id: patientId, summary_id: snapshot.id } },
        body,
      });
    }
  };

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2">
      {/* Шапка — как у «Врач v4/Секция»: подпись прописными и период справа. */}
      <h2 className="flex justify-between gap-3 px-4 text-xs font-semibold tracking-wide text-ink-muted uppercase">
        <span id={`${id}-title`}>Сводка с прошлого приёма</span>
        {view && (
          <span className="font-normal tracking-normal whitespace-nowrap normal-case">
            {view.period}
          </span>
        )}
      </h2>

      {query.isError && (
        <Notice tone="error">Не удалось загрузить сводку. Обновите страницу позже.</Notice>
      )}
      {query.isPending && <Skeleton />}
      {showFailure && <Notice tone="warning">{failure?.text}</Notice>}
      {summary && view && (
        <Body
          summary={summary}
          view={view}
          patientId={patientId}
          currentDoctorId={currentDoctorId}
          // Сообщение 409 `stale` уже сказало, что сводка обновляется: пометку не повторяем.
          quiet={showFailure && view.kind === "stale" && !view.retry && !view.refresh}
          retry={{
            busy: retry.isPending,
            text: retry.error ? retryError(retry.error).text : null,
            run: () => retry.mutate({ params: { path: { patient_id: patientId } } }),
          }}
          refresh={{ busy: query.isFetching, text: null, run: refresh }}
        />
      )}

      {view?.canConfirm && summary && (
        <div className="flex flex-col gap-2 px-4 pt-1">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              disabled={confirm.isPending}
              onClick={() => {
                confirm.reset();
                send(summary, { statements: summary.statements });
              }}
            >
              Подтвердить сводку
            </Button>
            <Button
              variant="quiet"
              disabled={confirm.isPending}
              onClick={() => {
                confirm.reset();
                setEditing(summary);
              }}
            >
              Изменить
            </Button>
          </div>
          <p className="flex items-center gap-2 text-[13px] leading-[18px] text-ink-muted">
            <Info aria-hidden className="size-5 shrink-0" strokeWidth={STROKE} />
            Сводку составила система по данным пациента. Проверьте утверждения по ссылкам.
          </p>
        </div>
      )}

      {editing && (
        <SummarySheet
          summary={editing}
          busy={confirm.isPending}
          error={failure && !failure.refetch ? failure.text : null}
          onConfirm={(body) => send(editing, body)}
          onClose={() => {
            // Врач закрыл лист сам: ошибка прошлой попытки к группе не относится.
            confirm.reset();
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}

function Body({
  summary,
  view,
  patientId,
  currentDoctorId,
  quiet,
  retry,
  refresh,
}: {
  summary: Summary;
  view: SummaryView;
  patientId: string;
  currentDoctorId: string;
  quiet: boolean;
  retry: NoticeAction;
  refresh: NoticeAction;
}) {
  const action = view.retry ? retry : view.refresh ? refresh : null;
  const button = action && (
    <Button variant="secondary" disabled={action.busy} onClick={action.run}>
      {view.retry ? "Повторить" : "Обновить"}
    </Button>
  );
  const note = action?.text && <span className="block font-normal">{action.text}</span>;

  if (view.kind === "pending") {
    return <Skeleton />;
  }
  if (view.kind === "empty") {
    return <Notice tone="info">{view.empty}</Notice>;
  }
  if (view.kind === "failed" || view.kind === "slow") {
    return (
      <Notice tone={view.kind === "failed" ? "error" : "waiting"} action={button}>
        {view.notice}
        {note}
      </Notice>
    );
  }

  const confirmed = view.kind === "confirmed";
  return (
    <div className={`rounded-xl bg-surface ${confirmed ? "" : PROPOSAL_OUTLINE}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-4 pt-4 pb-1">
        <StatusIcon status={confirmed ? "confirmed" : "proposed"} decorative />
        <p className={`text-sm font-semibold ${confirmed ? "text-success" : "text-primary-text"}`}>
          {confirmed ? "Подтверждено врачом" : "Предложение системы"}
        </p>
        {view.editedNote && (
          <p className="flex items-center gap-1 text-xs text-ink-muted">
            <PencilLine aria-hidden className="size-3.5" strokeWidth={STROKE} />
            {view.editedNote}
          </p>
        )}
        <p className="ml-auto text-xs text-ink-muted">
          {confirmed ? confirmedText(summary, currentDoctorId) : "ждёт подтверждения врача"}
        </p>
      </div>
      {view.notice && !quiet && (
        <div className="px-4 pt-2">
          <Notice tone={view.retry ? "error" : "warning"} action={button}>
            {view.notice}
            {note}
          </Notice>
        </div>
      )}
      <ul>
        {view.statements.map((statement, index) => (
          <StatementRow
            // Утверждения не переставляются: номер в списке устойчив.
            // biome-ignore lint/suspicious/noArrayIndexKey: у утверждения нет своего id
            key={index}
            statement={statement}
            patientId={patientId}
            last={index === view.statements.length - 1}
          />
        ))}
      </ul>
    </div>
  );
}

const TONES: Record<"info" | "waiting" | "warning" | "error", { Icon: LucideIcon; box: string }> = {
  info: { Icon: Info, box: "bg-surface text-ink" },
  waiting: { Icon: Clock, box: "bg-surface text-ink" },
  warning: { Icon: History, box: "bg-warning-soft text-warning" },
  error: { Icon: CircleAlert, box: "bg-danger-soft text-danger" },
};

/** Состояние группы: значок и слова, цвет только дополняет их (CLAUDE.md, «Интерфейс»). */
function Notice({
  tone,
  action,
  children,
}: {
  tone: keyof typeof TONES;
  action?: ReactNode;
  children: ReactNode;
}) {
  const { Icon, box } = TONES[tone];
  return (
    <div
      role={tone === "info" || tone === "waiting" ? "status" : "alert"}
      className={`flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-4 py-3 ${box}`}
    >
      <Icon aria-hidden className="size-5 shrink-0" strokeWidth={STROKE} />
      <div className="min-w-0 flex-1 text-sm font-medium">{children}</div>
      {action}
    </div>
  );
}

/** Черновик готовится в worker: скелетон группы (кадр 05б, «Состояния»). */
function Skeleton() {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface">
      <p role="status" className="sr-only">
        Система составляет сводку
      </p>
      {[0, 1, 2].map((row) => (
        <div key={row} aria-hidden className="flex gap-3 px-4 py-3 motion-safe:animate-pulse">
          <div className="size-5 shrink-0 rounded bg-line" />
          <div className="flex flex-1 flex-col gap-2">
            <div className="h-4 w-3/4 rounded bg-line" />
            <div className="h-3.5 w-1/2 rounded bg-line" />
          </div>
          <div className="h-3.5 w-10 rounded bg-line" />
        </div>
      ))}
    </div>
  );
}
