import {
  $api,
  fetchClient,
  isSessionExpiryPending,
  MessagingLocalError,
} from "@compass/api-client";
import {
  Alert,
  Button,
  clearMessagingOwnerAfterConfirmation,
  getMessagingLocalCounts,
  lockMessagingAccess,
} from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { LogOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { clearCachedPatientPreferences } from "../preferences/cache";
import { getAssessmentStore, type PatientSession } from "../session";

/**
 * Выход с подтверждением удаления локальных ответов. `appearance="link"` — текстовая кнопка
 * на «Сегодня»; `"row"` — строка профиля во всю ширину (кадр «Пациент · 29»).
 */
export function PatientLogoutControl({
  session,
  appearance = "link",
}: {
  session: PatientSession;
  appearance?: "link" | "row";
}) {
  const ownerId =
    session.status === "online"
      ? session.user.id
      : session.status === "offline"
        ? session.ownerId
        : null;
  const logout = $api.useMutation("post", "/api/v1/auth/logout");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [open, setOpen] = useState(false);
  const [counts, setCounts] = useState<{
    drafts: number;
    outbox: number;
    audio: number;
    chatDrafts: number;
    chatUnsent: number;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  async function requestLogout(): Promise<void> {
    if (!ownerId) return;
    setError(null);
    try {
      const count = await (await getAssessmentStore()).getOwnerDataCounts(ownerId);
      let chatCounts = { drafts: 0, unsent: 0 };
      try {
        chatCounts = await getMessagingLocalCounts();
      } catch (error) {
        if (!(error instanceof MessagingLocalError) || error.code !== "messaging_locked")
          throw error;
      }
      if (count.drafts + count.outbox + count.audio + chatCounts.drafts + chatCounts.unsent > 0) {
        setCounts({ ...count, chatDrafts: chatCounts.drafts, chatUnsent: chatCounts.unsent });
        setOpen(true);
      } else {
        await finishLogout(false);
      }
    } catch {
      setError("Не удалось проверить локальные ответы. Выход не выполнен.");
    }
  }

  async function finishLogout(deleteLocalData: boolean): Promise<void> {
    if (!ownerId) return;
    setError(null);
    try {
      await logout.mutateAsync({});
      await clearCachedPatientPreferences(ownerId).catch(() => undefined);
      await lockMessagingAccess();
      const store = await getAssessmentStore();
      if (deleteLocalData) {
        await clearMessagingOwnerAfterConfirmation(ownerId);
        await store.clearOwnerDataAfterConfirmation(ownerId);
      } else await store.clearOwnerPointer(ownerId);
      await queryClient.cancelQueries();
      queryClient.clear();
      setOpen(false);
      await navigate({ to: "/login", replace: true });
    } catch {
      if (isSessionExpiryPending(fetchClient)) return;
      setError("Не удалось завершить выход. Локальные ответы сохранены.");
    }
  }

  return (
    <section
      className={appearance === "row" ? "patient-logout patient-logout--row" : "patient-logout"}
    >
      {error && <Alert tone="error">{error}</Alert>}
      {appearance === "row" ? (
        <button
          type="button"
          className="patient-logout__row"
          onClick={() => void requestLogout()}
          disabled={logout.isPending}
        >
          <span className="patient-logout__icon">
            <LogOut aria-hidden="true" strokeWidth={1.75} />
          </span>
          <span>Выйти</span>
        </button>
      ) : (
        <Button variant="link" onClick={() => void requestLogout()} disabled={logout.isPending}>
          Выйти
        </Button>
      )}
      <dialog
        ref={dialogRef}
        className="patient-confirm-dialog"
        aria-labelledby="patient-logout-title"
        onCancel={(event) => {
          event.preventDefault();
          setOpen(false);
        }}
        onClose={() => setOpen(false)}
      >
        <h2 id="patient-logout-title" className="type-patient-title">
          Выход с локальными ответами
        </h2>
        {counts && (
          <p className="patient-body-small">
            На устройстве сохранено {counts.drafts} черновиков, {counts.outbox} операций и{" "}
            {counts.audio} аудиозаписей, {counts.chatDrafts} черновиков чата и {counts.chatUnsent}{" "}
            неотправленных сообщений. Удаление нельзя отменить.
          </p>
        )}
        {error && (
          <p role="alert" className="patient-inline-error">
            {error}
          </p>
        )}
        <div className="patient-confirm-dialog__actions">
          <Button
            variant="secondary"
            autoFocus
            disabled={logout.isPending}
            onClick={() => setOpen(false)}
          >
            Остаться
          </Button>
          <Button disabled={logout.isPending} onClick={() => void finishLogout(true)}>
            Удалить локальные ответы и выйти
          </Button>
        </div>
      </dialog>
    </section>
  );
}
