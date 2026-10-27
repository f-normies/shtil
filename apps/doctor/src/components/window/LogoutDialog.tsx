import { Button } from "@compass/ui";
import { useEffect, useId, useRef } from "react";

/**
 * «Выйти из кабинета?» — модальный `<dialog>` (как `ReferralSheet`): Esc закрывает, фокус
 * уходит в окно и возвращается на «Выйти» в меню, Tab не выходит за окно. Пока идёт
 * выход, Esc и клик по затемнению не закрывают окно.
 */
export function LogoutDialog({
  counts,
  countsLoading,
  clearLocal,
  pending,
  failed,
  onClearLocalChange,
  onConfirm,
  onClose,
}: {
  counts: { drafts: number; unsent: number };
  countsLoading: boolean;
  clearLocal: boolean;
  pending: boolean;
  failed: boolean;
  onClearLocalChange: (value: boolean) => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: Esc закрывает <dialog> штатно, клик — только по затемнению
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      aria-modal="true"
      onClose={onClose}
      onCancel={(e) => pending && e.preventDefault()}
      onClick={(e) => e.target === ref.current && !pending && ref.current?.close()}
      className="m-auto w-full max-w-md rounded-2xl bg-surface p-0 text-ink shadow-xl backdrop:bg-black/30"
    >
      <div className="p-6">
        <h2 id={`${id}-title`} className="text-lg font-semibold text-ink">
          Выйти из кабинета?
        </h2>
        <p className="mt-2 text-sm text-ink-muted">
          {countsLoading
            ? "Проверяем локальные сообщения…"
            : `Черновики на этом устройстве: ${counts.drafts}. Неотправленные сообщения: ${counts.unsent}.`}
        </p>
        <label className="mt-4 flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={clearLocal}
            onChange={(event) => onClearLocalChange(event.currentTarget.checked)}
          />
          Удалить эти данные с устройства после успешного выхода
        </label>
        {failed && (
          <p role="alert" className="mt-3 text-sm text-danger">
            Не удалось выйти. Попробуйте ещё раз.
          </p>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" disabled={pending} onClick={() => ref.current?.close()}>
            Остаться
          </Button>
          <Button disabled={pending || countsLoading} onClick={onConfirm}>
            {pending ? "Выходим…" : "Выйти"}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
