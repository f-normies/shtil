import type { Schemas } from "@compass/api-client";

export type PatientHelp = Schemas["PatientHelpOut"];
type StorageLike = Pick<Storage, "getItem" | "setItem">;

/** Что показывает экран: данные сервера или запасной вариант без сети и без кэша. */
export type HelpContent = Pick<PatientHelp, "title" | "contacts" | "sources"> & {
  text: string | null;
  checked_on: string | null;
};

const KEY = "compass.patient-help";

/**
 * Экран без сети и без сохранённых данных (спека #57, раздел 6): один номер 112, без текста
 * и источников. Это единственный номер в коде PWA, тест сверяет его с knowledge/.
 */
export const FALLBACK_HELP: HelpContent = {
  title: "Если сейчас очень тяжело",
  text: null,
  checked_on: null,
  sources: [],
  contacts: [
    { id: "emergency", number: "112", label: "Позвонить 112", primary: true, sources: [] },
  ],
};

function browserStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Последний ответ `GET /patient-help`. В нём нет данных пациента, поэтому он лежит
 * в `localStorage`: сервис-воркер ответы API не кэширует.
 */
export function readStoredHelp(storage: StorageLike | null = browserStorage()): PatientHelp | null {
  try {
    const raw = storage?.getItem(KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return isHelp(value) ? value : null;
  } catch {
    return null;
  }
}

/** Сохранить ответ сервера; хранилище недоступно — экран возьмёт запасной вариант. */
export function storeHelp(help: PatientHelp, storage: StorageLike | null = browserStorage()): void {
  try {
    storage?.setItem(KEY, JSON.stringify(help));
  } catch {
    // Приватный режим или запрет хранилища: данные придут с сервера при следующем входе.
  }
}

function isHelp(value: unknown): value is PatientHelp {
  if (!value || typeof value !== "object") return false;
  const help = value as Partial<PatientHelp>;
  return (
    typeof help.title === "string" &&
    typeof help.text === "string" &&
    Array.isArray(help.sources) &&
    Array.isArray(help.triggers) &&
    Array.isArray(help.contacts) &&
    help.contacts.some((contact) => contact.primary && typeof contact.number === "string")
  );
}
