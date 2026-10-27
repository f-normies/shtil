import { homePathFor } from "@compass/api-client";
import { Alert, Brand, LoginForm } from "@compass/ui";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { getOfflineAssessmentDenialMessage } from "../assessments/offline-access";
import { ConsentRevokedNotice } from "../consent/ConsentRevokedNotice";
import {
  CONSENT_REVOKE_UNKNOWN_TEXT,
  isConsentRevokedError,
  parseLoginSearch,
} from "../consent/revoke";
import { currentUser, ME_KEY } from "../session";

export const Route = createFileRoute("/login")({
  // ?reason=consent_revoked — переход с экрана отзыва согласия (спека #112, раздел 3.6).
  validateSearch: parseLoginSearch,
  beforeLoad: async ({ context }) => {
    let user: Awaited<ReturnType<typeof currentUser>>;
    try {
      user = await currentUser(context.queryClient);
    } catch (error) {
      if (!(error instanceof TypeError) || !getOfflineAssessmentDenialMessage()) throw error;
      return;
    }
    if (user) {
      if (user.role === "patient") throw redirect({ to: "/", replace: true });
      throw redirect({
        href: homePathFor(user.role),
        reloadDocument: true,
        replace: true,
      });
    }
  },
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const { queryClient } = Route.useRouteContext();
  const { reason } = Route.useSearch();
  const [accessMessage] = useState(getOfflineAssessmentDenialMessage);
  return (
    <main className="patient-login">
      <div className="patient-login__content">
        <Brand persona="patient" className="patient-login__brand" />
        <div className="patient-login__intro">
          <h1>С возвращением</h1>
          <p>Войдите, чтобы сделать отметку и написать врачу.</p>
        </div>
        {accessMessage && (
          <div className="patient-login__access-message">
            <Alert tone="error">{accessMessage}</Alert>
          </div>
        )}
        <LoginForm
          persona="patient"
          notice={
            reason === "consent_revoked" ? (
              <ConsentRevokedNotice />
            ) : reason === "consent_revoke_unknown" ? (
              <ConsentRevokedNotice text={CONSENT_REVOKE_UNKNOWN_TEXT} />
            ) : undefined
          }
          explainError={(error) =>
            isConsentRevokedError(error) ? <ConsentRevokedNotice /> : undefined
          }
          onSuccess={(user) => {
            queryClient.setQueryData(ME_KEY, user);
            if (user.role === "patient") {
              void navigate({ to: "/", replace: true });
            } else {
              void navigate({ href: homePathFor(user.role), reloadDocument: true, replace: true });
            }
          }}
        />
      </div>
      <p className="patient-login__footer">Нет аккаунта? Попросите врача прислать приглашение.</p>
    </main>
  );
}
