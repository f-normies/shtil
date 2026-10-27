import { useRegisterSW } from "virtual:pwa-register/react";
import { Button } from "@compass/ui";
import { localAssessmentWritePending } from "./local-write-state";
import { useLocalAssessmentWritePending } from "./use-local-assessment-write";

export function PwaUpdateNotice() {
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW();
  const writePending = useLocalAssessmentWritePending();

  if (!needRefresh) return null;

  function applyUpdate(): void {
    if (localAssessmentWritePending()) return;
    void updateServiceWorker();
  }

  return (
    <div
      role="status"
      className="mx-auto flex w-full max-w-2xl flex-col gap-3 border-b border-line bg-surface px-4 py-3 text-sm text-ink sm:flex-row sm:items-center sm:justify-between"
    >
      <p aria-live="polite">
        {writePending
          ? "Дождитесь завершения сохранения ответов."
          : "Доступно обновление приложения."}
      </p>
      <Button disabled={writePending} onClick={applyUpdate}>
        Обновить приложение
      </Button>
    </div>
  );
}
