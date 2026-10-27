import { $api, homePathFor } from "@compass/api-client";
import { Button } from "@compass/ui";
import diaryIcon from "@compass/ui/assets/ikonka-dnevnik.svg";
import profileIcon from "@compass/ui/assets/ikonka-profil.svg";
import exitIcon from "@compass/ui/assets/ikonka-vykhod.svg";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { lockLocalAccessOnExpiry } from "../assessments/session-expiry";
import { ConsentIcon } from "../consent/ConsentIcon";
import {
  type LoginSearch,
  REVOKE_CONSEQUENCES,
  revokeErrorText,
  unsentNotice,
} from "../consent/revoke";
import { clearCachedPatientPreferences } from "../preferences/cache";
import { describeAcceptedConsent } from "../preferences/consent-label";
import { useAcceptedConsent } from "../preferences/hooks";
import { getAssessmentStore, resolvePatientSession } from "../session";

const ICONS: Record<(typeof REVOKE_CONSEQUENCES)[number]["id"], string> = {
  data: diaryIcon,
  login: exitIcon,
  record: diaryIcon,
  doctor: profileIcon,
};

export const Route = createFileRoute("/consent/revoke")({
  beforeLoad: async ({ context }) => {
    const session = await resolvePatientSession(context.queryClient);
    if (session.status === "unauthenticated" || session.status === "network-unavailable") {
      throw redirect({ to: "/login", replace: true });
    }
    if (session.status === "other-role") {
      throw redirect({
        href: homePathFor(session.user.role),
        reloadDocument: true,
        replace: true,
      });
    }
    return { session };
  },
  component: RevokeConsentPage,
});

/**
 * Отзыв согласия (спека #112, раздел 3.6; кадр «Пациент · 30»). Что будет дальше — простыми
 * словами, отзыв — после второго подтверждения. Открывается из «Профиля» (#98), «Отмена»
 * возвращает туда же.
 */
function RevokeConsentPage() {
  const { session, queryClient } = Route.useRouteContext();
  const navigate = useNavigate();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [confirming, setConfirming] = useState(false);
  const [unsent, setUnsent] = useState<string | null>(null);
  const ownerId = session.status === "online" ? session.user.id : session.ownerId;
  const consent = useAcceptedConsent(session);
  const finish = async (reason: LoginSearch["reason"]) => {
    // Пациент подтвердил отзыв; после завершения сессии очищаем неотправленные ответы.
    // Не удалось удалить — закрываем их, как при завершённой сессии.
    await clearCachedPatientPreferences(ownerId).catch(() => undefined);
    try {
      await (await getAssessmentStore()).clearOwnerDataAfterConfirmation(ownerId, true);
    } catch {
      await lockLocalAccessOnExpiry(getAssessmentStore, ownerId);
    }
    await queryClient.cancelQueries();
    queryClient.clear();
    await navigate({ to: "/login", search: { reason }, replace: true });
  };
  const revoke = $api.useMutation("post", "/api/v1/me/consent/revoke", {
    onSuccess: () => finish("consent_revoked"),
    onError: async (error) => {
      // Ответ прежней попытки мог потеряться после коммита. 401 доказывает только
      // завершение сессии: очищаем устройство, состояние согласия не объявляем.
      if (
        typeof error === "object" &&
        error !== null &&
        "status" in error &&
        error.status === 401
      ) {
        await finish("consent_revoke_unknown");
      }
    },
  });

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (confirming && !dialog.open) {
      dialog.showModal();
      // React не выводит атрибут autofocus, и showModal() выбрал бы первую кнопку — «Да, отозвать».
      dialog.querySelector<HTMLButtonElement>("[data-initial-focus]")?.focus();
    }
    if (!confirming && dialog.open) dialog.close();
  }, [confirming]);

  const openConfirmation = async () => {
    try {
      setUnsent(unsentNotice(await (await getAssessmentStore()).getOwnerDataCounts(ownerId)));
    } catch {
      setUnsent(unsentNotice(null));
    }
    setConfirming(true);
  };

  const cancelConfirmation = () => {
    if (revoke.isPending) return;
    revoke.reset();
    setConfirming(false);
  };

  return (
    <main className="consent-revoke">
      <h1 className="consent-revoke__title">Отозвать согласие?</h1>
      {consent.data && (
        <p className="consent-revoke__chip">{describeAcceptedConsent(consent.data)}</p>
      )}
      <ul className="consent-revoke__card">
        {REVOKE_CONSEQUENCES.map((item) => (
          <li key={item.id} className="consent-revoke__item">
            <span className="consent-revoke__badge">
              <ConsentIcon src={ICONS[item.id]} />
            </span>
            <div>
              <p className="consent-revoke__label">{item.title}</p>
              <p className="consent-revoke__text">{item.text}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="consent-revoke__actions">
        <Button onClick={() => void openConfirmation()}>Отозвать согласие</Button>
        <Button variant="quiet" onClick={() => void navigate({ to: "/profile" })}>
          Отмена
        </Button>
      </div>
      <dialog
        ref={dialogRef}
        role="alertdialog"
        aria-labelledby="revoke-confirm-title"
        aria-describedby="revoke-confirm-text"
        aria-modal="true"
        onCancel={(event) => {
          event.preventDefault();
          cancelConfirmation();
        }}
        onClose={() => setConfirming(false)}
        className="consent-revoke__dialog"
      >
        <h2 id="revoke-confirm-title">Точно отозвать согласие?</h2>
        <p id="revoke-confirm-text">
          Вход закроется сразу на всех устройствах. Вернуться можно только через врача.
        </p>
        {unsent && <p className="consent-revoke__unsent">{unsent}</p>}
        {revoke.isError && (
          <p role="alert" className="consent-revoke__error">
            {revokeErrorText(revoke.error)}
          </p>
        )}
        <div className="consent-revoke__dialog-actions">
          <Button
            loading={revoke.isPending}
            onClick={() => {
              if (revoke.isPending) return;
              revoke.mutate({});
            }}
          >
            {revoke.isPending ? "Отзываем…" : "Да, отозвать"}
          </Button>
          <Button
            variant="quiet"
            data-initial-focus
            disabled={revoke.isPending}
            onClick={cancelConfirmation}
          >
            Отмена
          </Button>
        </div>
      </dialog>
    </main>
  );
}
