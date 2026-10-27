import { Button, Panel } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getAssessmentStore } from "../session";
import type { AssessmentDraft } from "./types";

export function AssessmentDraftVersionRecovery({
  ownerId,
  scaleId,
  currentVersion,
  draft,
}: {
  ownerId: string;
  scaleId: string;
  currentVersion: number;
  draft: AssessmentDraft;
}) {
  const queryClient = useQueryClient();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const oldVersion = draft.definition_snapshot.scale.version;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (confirmationOpen && !dialog.open) dialog.showModal();
    if (!confirmationOpen && dialog.open) dialog.close();
  }, [confirmationOpen]);

  async function discardAndStartCurrentVersion(): Promise<void> {
    setError(null);
    try {
      const store = await getAssessmentStore();
      const result = await store.discardDraft({
        ownerId,
        scaleId,
        expectedRevision: draft.revision,
      });
      const draftKey = ["assessment-draft", ownerId, scaleId] as const;
      if (result.status === "conflict") {
        queryClient.setQueryData(draftKey, result.currentDraft);
        setError("Черновик изменился в другой вкладке. Проверьте сохранённые ответы.");
        setConfirmationOpen(false);
        return;
      }
      queryClient.setQueryData(draftKey, null);
      setConfirmationOpen(false);
    } catch {
      setError("Не удалось удалить старый черновик. Он остался на устройстве.");
    }
  }

  return (
    <>
      <Panel title={`Сохранённый черновик версии ${oldVersion}`}>
        <p className="text-sm text-ink-muted">
          Опросник обновился до версии {currentVersion}. Ответы нельзя перенести автоматически.
          Старый черновик останется без изменений, пока вы явно не подтвердите удаление.
        </p>
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-ink">
          {draft.definition_snapshot.scale.items.map((item) => {
            const answer = draft.answers[item.id];
            if (answer === undefined) return null;
            const option = item.options.find((candidate) => candidate.value === answer);
            return (
              <li key={item.id}>
                {item.text}: {option?.text ?? `Ответ ${answer}`}
              </li>
            );
          })}
        </ul>
        {error && (
          <div role="alert" className="mt-3 text-sm text-danger">
            {error}
          </div>
        )}
        <Button className="mt-4" onClick={() => setConfirmationOpen(true)}>
          Начать версию {currentVersion} без старого черновика
        </Button>
      </Panel>
      <dialog
        ref={dialogRef}
        role="alertdialog"
        aria-labelledby="discard-old-draft-title"
        aria-modal="true"
        onCancel={(event) => {
          event.preventDefault();
          setConfirmationOpen(false);
        }}
        onClose={() => setConfirmationOpen(false)}
        className="m-auto w-[calc(100%-2rem)] max-w-md rounded-lg border border-line bg-surface p-5 text-ink shadow-xl backdrop:bg-black/40"
      >
        <h2 id="discard-old-draft-title" className="text-lg font-semibold">
          Удалить черновик версии {oldVersion}?
        </h2>
        <p className="mt-3 text-sm text-ink-muted">
          Сохранённые ответы будут удалены с этого устройства. Версия {currentVersion} начнётся без
          выбранных ответов.
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" autoFocus onClick={() => setConfirmationOpen(false)}>
            Оставить старый черновик
          </Button>
          <Button onClick={() => void discardAndStartCurrentVersion()}>
            Удалить и начать версию {currentVersion}
          </Button>
        </div>
      </dialog>
    </>
  );
}
