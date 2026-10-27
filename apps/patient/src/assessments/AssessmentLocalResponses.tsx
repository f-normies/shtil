import { Alert, Button, Panel } from "@compass/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { getAssessmentStore } from "../session";
import { DefinitionSources } from "./AssessmentCatalog";
import { canRetryBlockedSubmission } from "./queue-policy";
import type { OutboxRecord } from "./types";

export function AssessmentLocalResponses({
  ownerId,
  online,
  onRetry,
}: {
  ownerId: string;
  online: boolean;
  onRetry: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [recordToDelete, setRecordToDelete] = useState<OutboxRecord | null>(null);
  const [deleteError, setDeleteError] = useState(false);
  const [blockedActionError, setBlockedActionError] = useState<string | null>(null);
  const deleteDialogRef = useRef<HTMLDialogElement>(null);
  const outbox = useQuery({
    queryKey: ["assessment-local", ownerId],
    queryFn: async () => (await getAssessmentStore()).getOutboxForOwner(ownerId),
    staleTime: 0,
    retry: false,
  });

  useEffect(() => {
    const dialog = deleteDialogRef.current;
    if (!dialog) return;
    if (recordToDelete && !dialog.open) dialog.showModal();
    if (!recordToDelete && dialog.open) dialog.close();
  }, [recordToDelete]);

  async function confirmDelete(): Promise<void> {
    if (!recordToDelete) return;
    try {
      await (await getAssessmentStore()).removeOutboxRecord(ownerId, recordToDelete.submission_id);
      setRecordToDelete(null);
      await queryClient.invalidateQueries({ queryKey: ["assessment-local", ownerId] });
    } catch {
      setDeleteError(true);
    }
  }

  async function resendBlocked(record: OutboxRecord): Promise<void> {
    setBlockedActionError(null);
    try {
      const requeued = await (await getAssessmentStore()).requeueBlockedOutboxRecord(
        ownerId,
        record.submission_id,
      );
      await queryClient.invalidateQueries({ queryKey: ["assessment-local", ownerId] });
      if (requeued) onRetry();
    } catch {
      setBlockedActionError("Не удалось повторить отправку. Ответ остался сохранён на устройстве.");
    }
  }

  async function restoreAnswers(record: OutboxRecord): Promise<void> {
    setBlockedActionError(null);
    try {
      const result = await (await getAssessmentStore()).restoreOutboxToDraft(
        ownerId,
        record.submission_id,
      );
      if (result.status === "draft-exists") {
        setBlockedActionError("Сначала завершите сохранённый черновик по этому опроснику.");
        return;
      }
      if (result.status !== "restored") {
        setBlockedActionError("Ответ больше нельзя вернуть в форму. Обновите страницу.");
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["assessment-local", ownerId] });
      await queryClient.invalidateQueries({
        queryKey: ["assessment-draft", ownerId, record.payload.scale_id],
      });
      await navigate({
        to: "/assessments/$scaleId",
        params: { scaleId: record.payload.scale_id },
      });
    } catch {
      setBlockedActionError(
        "Не удалось открыть ответы для исправления. Они остались сохранены на устройстве.",
      );
    }
  }

  if (outbox.isPending) return null;
  if (outbox.isError) {
    return (
      <Panel title="Ожидают отправки">
        <Alert tone="error">
          Не удалось прочитать сохранённые ответы. Они остались на устройстве. Повторите чтение.
        </Alert>
        <Button className="mt-3" variant="secondary" onClick={() => void outbox.refetch()}>
          Повторить чтение
        </Button>
      </Panel>
    );
  }
  if (!outbox.data?.length) return null;

  const pending = outbox.data.filter((record) => record.state === "pending");
  const blocked = outbox.data.filter((record) => record.state === "blocked");
  return (
    <div className="flex flex-col gap-4">
      {pending.length > 0 && (
        <Panel title="Ожидают отправки">
          <p className="mb-3 text-sm text-ink-muted">
            Эти ответы сохранены на устройстве. Врач увидит их после подтверждения сервером.
          </p>
          <ul className="flex flex-col gap-4">
            {pending.map((record) => (
              <OutboxAnswer key={record.submission_id} record={record} />
            ))}
          </ul>
          {online && (
            <Button className="mt-4" variant="secondary" onClick={onRetry}>
              Повторить отправку
            </Button>
          )}
        </Panel>
      )}
      {blocked.length > 0 && (
        <Panel title="Нужно проверить">
          <Alert tone="error">
            Часть ответов не удалось отправить автоматически. Проверьте их перед дальнейшими
            действиями.
          </Alert>
          <ul className="mt-3 flex flex-col gap-4">
            {blocked.map((record) => (
              <OutboxAnswer key={record.submission_id} record={record} />
            ))}
          </ul>
          <ul className="mt-4 flex flex-col gap-3">
            {blocked.map((record) => (
              <li key={record.submission_id} className="flex flex-col gap-2 sm:flex-row">
                {online && canRetryBlockedSubmission(record.last_error) && (
                  <Button onClick={() => void resendBlocked(record)}>Повторить отправку</Button>
                )}
                {record.last_error?.status === 422 && (
                  <Button onClick={() => void restoreAnswers(record)}>Исправить ответы</Button>
                )}
                {record.last_error?.code === "scale_version_changed" && (
                  <Link
                    to="/assessments/$scaleId"
                    params={{ scaleId: record.payload.scale_id }}
                    className="inline-flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                  >
                    Заполнить актуальную версию
                  </Link>
                )}
                <Button
                  variant="secondary"
                  onClick={() => {
                    setDeleteError(false);
                    setRecordToDelete(record);
                  }}
                >
                  {record.last_error?.code === "scale_version_changed"
                    ? "Удалить конфликтный ответ"
                    : "Удалить сохранённый ответ"}
                </Button>
              </li>
            ))}
          </ul>
          {blockedActionError && (
            <p role="alert" className="mt-3 text-sm text-danger">
              {blockedActionError}
            </p>
          )}
        </Panel>
      )}
      <dialog
        ref={deleteDialogRef}
        role="alertdialog"
        aria-labelledby="delete-assessment-title"
        aria-modal="true"
        onCancel={(event) => {
          event.preventDefault();
          setRecordToDelete(null);
        }}
        onClose={() => setRecordToDelete(null)}
        className="m-auto w-[calc(100%-2rem)] max-w-md rounded-lg border border-line bg-surface p-5 text-ink shadow-xl backdrop:bg-black/40"
      >
        <h2 id="delete-assessment-title" className="text-lg font-semibold">
          Удаление сохранённого ответа
        </h2>
        {recordToDelete && (
          <>
            <p className="mt-3 text-sm text-ink-muted">
              Ответ по версии {recordToDelete.payload.scale_version} и его исходная форма будут
              удалены с этого устройства. Это действие нельзя отменить.
            </p>
            <ul className="mt-3 flex flex-col gap-1 text-sm">
              {recordToDelete.definition_snapshot.scale.items.map((item) => {
                const value = recordToDelete.payload.answers[item.id];
                const option = item.options.find((candidate) => candidate.value === value);
                return (
                  <li key={item.id}>
                    {item.text} — {option?.text ?? `Значение ${value}`}
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {deleteError && (
          <p role="alert" className="mt-3 text-sm text-danger">
            Не удалось удалить локальный ответ. Он остался сохранённым на устройстве.
          </p>
        )}
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" autoFocus onClick={() => setRecordToDelete(null)}>
            Оставить ответ
          </Button>
          <Button onClick={() => void confirmDelete()}>Удалить ответ</Button>
        </div>
      </dialog>
    </div>
  );
}

function OutboxAnswer({ record }: { record: OutboxRecord }) {
  const definition = record.definition_snapshot;
  return (
    <li className="min-w-0 border-t border-line pt-3 first:border-0 first:pt-0">
      <h3 className="font-semibold text-ink">{definition.scale.title}</h3>
      <p className="text-sm text-ink-muted">
        Версия {definition.scale.version}. Заполнено {formatDate(record.payload.completed_at)}.
      </p>
      {record.last_error && (
        <p className="mt-2 text-sm font-medium text-danger">
          Отправка остановлена: {errorMessage(record.last_error.code)}
        </p>
      )}
      <ul className="mt-2 flex flex-col gap-1 text-sm">
        {definition.scale.items.map((item) => {
          const value = record.payload.answers[item.id];
          const option = item.options.find((candidate) => candidate.value === value);
          return (
            <li key={item.id}>
              <span>{item.text}</span>
              <span className="mx-1">{" — "}</span>
              <span>{option?.text ?? `Значение ${value}`}</span>
            </li>
          );
        })}
      </ul>
      <DefinitionSources sources={definition.sources} />
    </li>
  );
}

function errorMessage(code: string): string {
  switch (code) {
    case "scale_version_changed":
      return "версия опросника изменилась. Ответы сохранены по исходной форме.";
    case "scale_unavailable":
      return "опросник сейчас недоступен.";
    case "lead_doctor_missing":
      return "лечащий врач пока не назначен.";
    case "submission_conflict":
      return "сервер обнаружил конфликт отправки.";
    default:
      return "сервер отклонил ответ. Ответы сохранены на устройстве.";
  }
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}
