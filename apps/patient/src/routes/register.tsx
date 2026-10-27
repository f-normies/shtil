import { $api, retryAfterText } from "@compass/api-client";
import { Alert, Brand, Button, Checkbox, TextField } from "@compass/ui";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { type FormEvent, useState } from "react";
import {
  FULL_NAME_MAX_LENGTH,
  type RegisterFieldErrors,
  registerFieldErrors,
} from "../auth/register-validation";
import { ConsentDocumentScreen } from "../consent/ConsentDocumentScreen";
import { ME_KEY } from "../session";

export const Route = createFileRoute("/register")({
  validateSearch: (search: Record<string, unknown>) => ({
    invite: typeof search.invite === "string" ? search.invite : "",
  }),
  component: RegisterPage,
});

const ERRORS: Record<number, string> = {
  409: "этот email уже зарегистрирован. Войдите или используйте другой адрес.",
  410: "ссылка-приглашение недействительна, уже использована или истекла. Попросите у врача новую.",
  422: `проверьте поля: ФИО — от 1 до ${FULL_NAME_MAX_LENGTH} символов, пароль — не короче 8 символов, email — корректный адрес.`,
};

/** Регистрация по приглашению (кадр «Пациент · 02»); согласие — отдельным экраном (кадр 03). */
function RegisterPage() {
  const { invite } = Route.useSearch();
  const { queryClient } = Route.useRouteContext();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [acceptedConsentVersion, setAcceptedConsentVersion] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<RegisterFieldErrors>({});
  const [readingConsent, setReadingConsent] = useState(false);
  const consent = $api.useQuery("get", "/api/v1/patients/consent", undefined, {
    enabled: Boolean(invite),
    retry: false,
    staleTime: 0,
  });
  const register = $api.useMutation("post", "/api/v1/patients/register", {
    onSuccess: (user) => {
      queryClient.setQueryData(ME_KEY, user);
      navigate({ to: "/" });
    },
    onError: (error) => {
      if (problemCode(error) !== "consent_version_mismatch") return;
      setAcceptedConsentVersion(null);
      void consent.refetch();
    },
  });

  if (!invite) {
    return (
      <Shell>
        <h1 className="mb-2 text-2xl font-semibold leading-[30px] tracking-tight">
          Нужна ссылка-приглашение
        </h1>
        <p className="text-[17px] leading-6 text-ink-muted">
          Аккаунт в Штиле создаётся только по приглашению лечащего врача. Попросите у врача ссылку и
          откройте её на этом телефоне.
        </p>
      </Shell>
    );
  }

  const consentVersionMismatch = problemCode(register.error) === "consent_version_mismatch";
  const rejectedConsentVersion = consentVersionMismatch
    ? register.variables?.body.consent_version
    : undefined;
  // Ошибка фонового обновления не скрывает прочитанный текст: версию всё равно сверит сервер.
  // Скрываем только редакцию, которую сервер уже отверг: принимать её снова бессмысленно.
  const consentFailed =
    consent.isError && (!consent.data || consent.data.version === rejectedConsentVersion);
  const shownConsent = consentFailed ? undefined : consent.data;
  const consentRefreshing = shownConsent !== undefined && consent.isFetching;
  const consentStatus = consent.isPending
    ? "Загружаем текст согласия…"
    : consentRefreshing
      ? "Обновляем текст согласия…"
      : null;
  // Принятая версия устарела после фонового обновления: объясняем, почему флажок снят.
  const consentChanged =
    acceptedConsentVersion !== null &&
    shownConsent !== undefined &&
    acceptedConsentVersion !== shownConsent.version;

  if (readingConsent && shownConsent) {
    return (
      <ConsentDocumentScreen
        document={shownConsent}
        onAccept={() => {
          setAcceptedConsentVersion(shownConsent.version);
          setReadingConsent(false);
        }}
        onBack={() => setReadingConsent(false)}
      />
    );
  }

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const version = shownConsent?.version;
    if (!version || version !== acceptedConsentVersion || consent.isFetching) return;
    if (register.isPending) return;
    const errors = registerFieldErrors({ fullName, email, password });
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    register.mutate({
      body: {
        invite_token: invite,
        full_name: fullName,
        email,
        password,
        consent_personal_data: true,
        consent_version: version,
      },
    });
  };

  const errorStatus = problemStatus(register.error);
  const wait = retryAfterText(register.error);
  return (
    <Shell>
      <p className="mb-4 rounded-3xl bg-surface px-4 py-4 text-[17px] leading-6 text-ink shadow-sm">
        Приглашение от лечащего врача
      </p>
      <h1 className="mb-4 text-2xl font-semibold leading-[30px] tracking-tight">
        Создайте аккаунт
      </h1>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {register.isError && !consentVersionMismatch && (
          <Alert tone="error">
            {wait !== null
              ? `слишком много попыток. Повторите ${wait}.`
              : ((errorStatus && ERRORS[errorStatus]) ?? "сервис недоступен, попробуйте позже.")}
          </Alert>
        )}
        {(consentVersionMismatch || consentChanged) && (
          <Alert tone="error">
            текст согласия изменился. Прочитайте новую редакцию и подтвердите согласие заново.
          </Alert>
        )}
        <TextField
          label="Фамилия, имя, отчество"
          hint="Как в направлении врача"
          autoComplete="name"
          required
          maxLength={FULL_NAME_MAX_LENGTH}
          error={fieldErrors.fullName}
          value={fullName}
          onChange={(e) => {
            setFullName(e.target.value);
            setFieldErrors((current) => ({ ...current, fullName: undefined }));
          }}
        />
        <TextField
          label="Электронная почта"
          hint="Сюда придёт подтверждение"
          type="email"
          autoComplete="email"
          required
          error={fieldErrors.email}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setFieldErrors((current) => ({ ...current, email: undefined }));
          }}
        />
        <TextField
          label="Пароль"
          type="password"
          autoComplete="new-password"
          hint="Не короче 8 символов."
          required
          minLength={8}
          error={fieldErrors.password}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
            setFieldErrors((current) => ({ ...current, password: undefined }));
          }}
        />
        <div>
          {/* Область статуса есть всегда, меняется только текст: программы чтения с экрана
              часто молчат, если область вставлена вместе с текстом. */}
          <p
            role="status"
            aria-live="polite"
            className={consentRefreshing ? "mb-2 text-sm text-ink-muted" : "text-sm text-ink-muted"}
          >
            {consentStatus}
          </p>
          {consentFailed && (
            <div className="flex flex-col items-start gap-2">
              <Alert tone="error">
                не удалось загрузить текст согласия. Проверьте связь и повторите попытку.
              </Alert>
              <Button
                type="button"
                variant="secondary"
                onClick={() => void consent.refetch()}
                disabled={consent.isFetching}
              >
                Повторить загрузку
              </Button>
            </div>
          )}
          {shownConsent && (
            <div className="flex flex-col gap-1">
              <Checkbox
                checked={acceptedConsentVersion === shownConsent.version}
                disabled={register.isPending || consent.isFetching}
                onChange={(event) => {
                  setAcceptedConsentVersion(event.target.checked ? shownConsent.version : null);
                }}
                required
              >
                <span className="text-[15px] leading-5">
                  Даю согласие на обработку персональных данных
                </span>
              </Checkbox>
              <button
                type="button"
                onClick={() => setReadingConsent(true)}
                className="ml-8 min-h-11 w-fit text-left text-[15px] leading-5 text-primary-text underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-primary"
              >
                Прочитать согласие
              </button>
            </div>
          )}
        </div>
        <Button
          type="submit"
          className="mt-2 w-full rounded-full"
          disabled={
            !shownConsent ||
            consent.isFetching ||
            acceptedConsentVersion !== shownConsent.version ||
            register.isPending
          }
        >
          {register.isPending ? "Создаём аккаунт…" : "Создать аккаунт"}
        </Button>
      </form>
    </Shell>
  );
}

function problemStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null || !("status" in error)) return undefined;
  const status = error.status;
  return typeof status === "number" ? status : undefined;
}

function problemCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null || !("code" in error)) return undefined;
  const code = error.code;
  return typeof code === "string" ? code : undefined;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-sm flex-col px-5 pb-6 pt-8">
      <Brand persona="patient" className="patient-auth-brand" />
      {children}
    </main>
  );
}
