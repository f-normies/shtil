import {
  $api,
  loginErrorMessage,
  loginFieldErrors,
  retryAfterSeconds,
  type User,
} from "@compass/api-client";
import { type FormEvent, type ReactNode, useEffect, useId, useState } from "react";
import type { BrandPersona } from "./Brand";
import { Button } from "./Button";
import { TextField } from "./fields";

type FieldErrors = { email?: string; password?: string };

function focusField(id: string, select = false): void {
  const input = document.getElementById(id);
  if (!(input instanceof HTMLInputElement)) return;
  input.focus();
  if (select) input.select();
}

/**
 * Форма входа по email и паролю (кадр «Врач v4 · 01»). После успеха отдаёт пользователя наружу.
 * Пустые поля не уходят на сервер; при 401 фокус в пароле и пароль выделен; после 429
 * «Войти» неактивна до конца `retry_after`. Фокус при открытии — в почте, если браузер
 * подставил почту — в пароле.
 * `explainError` — своё сообщение кабинета для ошибки; `undefined` — общее сообщение формы.
 * `notice` — пояснение над полями, пока форма не показала ошибку.
 */
export function LoginForm({
  onSuccess,
  persona = "doctor",
  explainError,
  notice,
}: {
  onSuccess: (user: User) => void;
  persona?: BrandPersona;
  explainError?: (error: unknown) => ReactNode | undefined;
  notice?: ReactNode;
}) {
  const id = useId();
  const emailId = `${id}-email`;
  const passwordId = `${id}-password`;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [lockedFor, setLockedFor] = useState<number | null>(null);
  const login = $api.useMutation("post", "/api/v1/auth/login", {
    onSuccess,
    onError: (error) => {
      const message = loginErrorMessage(error);
      focusField(message.field === "password" ? passwordId : emailId, message.field === "password");
      setLockedFor(retryAfterSeconds(error));
    },
  });

  useEffect(() => {
    const emailInput = document.getElementById(emailId);
    const filled = emailInput instanceof HTMLInputElement && emailInput.value !== "";
    focusField(filled ? passwordId : emailId);
  }, [emailId, passwordId]);

  useEffect(() => {
    if (lockedFor === null) return;
    const timer = setTimeout(() => setLockedFor(null), lockedFor * 1000);
    return () => clearTimeout(timer);
  }, [lockedFor]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (login.isPending || lockedFor !== null) return;
    const errors = loginFieldErrors({ email, password });
    setFieldErrors(errors);
    if (errors.email || errors.password) {
      focusField(errors.email ? emailId : passwordId);
      return;
    }
    login.mutate({ body: { email, password } });
  };

  const explained = login.isError ? explainError?.(login.error) : undefined;
  // Ошибка входа — подсказка под полем, без вины и без префикса (кадр «Пациент · 01»).
  const message = login.isError && explained === undefined ? loginErrorMessage(login.error) : null;
  const emailError = fieldErrors.email ?? (message?.field === "email" ? message.text : undefined);
  const passwordError =
    fieldErrors.password ?? (message?.field === "password" ? message.text : undefined);
  return (
    <form onSubmit={submit} className={`login-form login-form--${persona}`} noValidate>
      {!login.isError && notice}
      {explained}
      <div className="login-form__fields">
        <TextField
          id={emailId}
          label="Электронная почта"
          type="email"
          autoComplete="username"
          hint={persona === "patient" ? "Адрес, на который пришло приглашение" : undefined}
          error={emailError}
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            setFieldErrors((errors) => ({ ...errors, email: undefined }));
          }}
        />
        <div className="login-form__password">
          <TextField
            id={passwordId}
            label="Пароль"
            type="password"
            autoComplete="current-password"
            error={passwordError}
            required
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setFieldErrors((errors) => ({ ...errors, password: undefined }));
            }}
          />
        </div>
      </div>
      <Button type="submit" loading={login.isPending} disabled={lockedFor !== null}>
        {login.isPending ? "Входим…" : "Войти"}
      </Button>
    </form>
  );
}
