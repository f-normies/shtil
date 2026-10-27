/** Проверка полей регистрации до отправки; пределы — как в `PatientRegisterIn` на сервере. */

export const FULL_NAME_MAX_LENGTH = 200;
export const PASSWORD_MIN_LENGTH = 8;

export type RegisterFieldErrors = Partial<Record<"fullName" | "email" | "password", string>>;

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function registerFieldErrors(values: {
  fullName: string;
  email: string;
  password: string;
}): RegisterFieldErrors {
  const errors: RegisterFieldErrors = {};
  const fullName = values.fullName.trim();
  if (!fullName) errors.fullName = "Укажите фамилию, имя и отчество, как в направлении врача.";
  else if (fullName.length > FULL_NAME_MAX_LENGTH) {
    errors.fullName = `ФИО не длиннее ${FULL_NAME_MAX_LENGTH} символов.`;
  }
  if (!EMAIL_PATTERN.test(values.email.trim())) {
    errors.email = "Введите адрес почты, например name@example.ru.";
  }
  if (values.password.length < PASSWORD_MIN_LENGTH) {
    errors.password = `Пароль не короче ${PASSWORD_MIN_LENGTH} символов.`;
  }
  return errors;
}
