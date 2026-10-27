import { Button } from "@compass/ui";
import { Link } from "@tanstack/react-router";
import {
  ALargeSmall,
  Clock,
  FileCheck,
  type LucideIcon,
  Mic,
  Palette,
  Phone,
  Pill,
  User,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";
import { PatientBottomNavigation } from "../diary/PatientBottomNavigation";
import { describeAcceptedConsent } from "./consent-label";
import type { AccessiblePatientSession } from "./hooks";
import {
  patientOwnerId,
  useAcceptedConsent,
  useApplyPatientPreferences,
  usePatientPreferences,
  usePatientPrescriptions,
} from "./hooks";
import { moodPaletteLabel } from "./onboarding-flow";
import { PatientLogoutControl } from "./PatientLogoutControl";
import { pillAppearanceSummary } from "./pill-summary";

const textSizeLabels = {
  normal: "Обычный",
  large: "Крупный",
  extra_large: "Очень крупный",
} as const;

/** Профиль пациента (кадр «Пациент · 29»): строки настроек, помощь, данные и выход. */
export function ProfilePage({ session }: { session: AccessiblePatientSession }) {
  const ownerId = patientOwnerId(session);
  const preferences = usePatientPreferences(session);
  const prescriptions = usePatientPrescriptions(session);
  const consent = useAcceptedConsent(session);
  useApplyPatientPreferences(preferences.data);

  return (
    <>
      <main className="mx-auto flex min-h-dvh w-full max-w-lg flex-col gap-5 bg-canvas px-4 pb-28 pt-6 sm:py-8">
        <div>
          <h1 className="text-2xl font-semibold">Профиль</h1>
          <p className="mt-2 text-base leading-6 text-ink-muted">
            Настройки из первого входа можно поменять в любой момент.
          </p>
        </div>
        {session.status === "offline" && (
          <div
            role="status"
            className="rounded-xl border border-line bg-surface p-3 text-sm text-ink-muted"
          >
            Офлайн-режим. Сохранённые настройки доступны для чтения, изменения требуют подключения.
          </div>
        )}
        {preferences.isPending ? (
          <p role="status" className="text-sm text-ink-muted">
            Загружаем профиль…
          </p>
        ) : preferences.isError ? (
          <section className="flex flex-col gap-3 rounded-2xl border border-line bg-surface p-4">
            <p className="text-sm text-ink-muted">Не удалось загрузить настройки профиля.</p>
            <Button
              variant="secondary"
              onClick={() => void preferences.refetch()}
              disabled={preferences.isFetching}
            >
              Повторить
            </Button>
          </section>
        ) : preferences.data ? (
          <Group label="Настройки">
            <SettingRow
              setting="name"
              icon={User}
              title="Как к вам обращаться"
              value={preferences.data.display_name}
            />
            <SettingRow
              setting="reminder"
              icon={Clock}
              title="Время напоминания"
              value={`Каждый день в ${preferences.data.reminder_time}`}
            />
            <SettingRow
              setting="mood"
              icon={Palette}
              title="Цвета настроения"
              value={moodPaletteLabel(preferences.data.mood_palette)}
            />
            <SettingRow
              setting="story"
              icon={Mic}
              title="Как рассказывать о дне"
              value={preferences.data.story_mode === "voice" ? "Голосом" : "Текстом"}
            />
            {prescriptions.data && prescriptions.data.length > 0 && (
              <SettingRow
                setting="pills"
                icon={Pill}
                title="Как выглядят таблетки"
                value={pillAppearanceSummary(prescriptions.data)}
              />
            )}
            <SettingRow
              setting="text"
              icon={ALargeSmall}
              title="Размер текста"
              value={textSizeLabels[preferences.data.text_size]}
            />
          </Group>
        ) : null}
        {prescriptions.isError && (
          <section className="flex flex-col gap-2 rounded-xl border border-line bg-surface p-4">
            <p className="text-sm text-ink-muted">
              Не удалось проверить действующие назначения. Раздел с таблетками пока скрыт.
            </p>
            <Button
              variant="secondary"
              onClick={() => void prescriptions.refetch()}
              disabled={prescriptions.isFetching}
            >
              Повторить загрузку
            </Button>
          </section>
        )}
        <Link
          to="/onboarding"
          className="flex min-h-12 items-center justify-center rounded-xl bg-primary px-4 text-center font-medium text-white hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Пройти настройку заново
        </Link>
        <Group label="Помощь">
          <Row to="/help" icon={Phone} title="Если очень тяжело" value="Телефоны 112 и 103" />
        </Group>
        <Group label="Данные и вход">
          {consent.isPending ? (
            <p role="status" className="px-4 py-4 text-sm text-ink-muted">
              Загружаем сведения о согласии…
            </p>
          ) : consent.isError ? (
            <div className="flex flex-col gap-2 px-4 py-4">
              <p className="text-sm text-ink-muted">Не удалось загрузить сведения о согласии.</p>
              <Button
                variant="secondary"
                onClick={() => void consent.refetch()}
                disabled={consent.isFetching}
              >
                Повторить
              </Button>
            </div>
          ) : consent.data ? (
            consent.data.text ? (
              <Row
                to="/profile/$setting"
                params={{ setting: "consent" }}
                icon={FileCheck}
                title="Согласие на обработку данных"
                value={describeAcceptedConsent(consent.data)}
              />
            ) : (
              <div className="flex items-center gap-3.5 px-4 py-3.5">
                <RowIcon icon={FileCheck} />
                <div className="min-w-0">
                  <h3 className="text-[17px] leading-6 text-ink">Согласие на обработку данных</h3>
                  <p className="mt-0.5 text-[15px] leading-5 text-ink-muted">
                    {describeAcceptedConsent(consent.data)}
                  </p>
                  <p className="mt-1 text-sm text-ink-muted">Текст этой редакции недоступен.</p>
                </div>
              </div>
            )
          ) : null}
          <Row to="/consent/revoke" icon={XCircle} title="Отозвать согласие" />
        </Group>
        <div className="overflow-hidden rounded-3xl border border-line bg-surface shadow-sm">
          <PatientLogoutControl ownerId={ownerId} />
        </div>
      </main>
      <PatientBottomNavigation />
    </>
  );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className="flex flex-col gap-2">
      <h2 className="px-1 pt-2 text-[13px] font-medium tracking-wide text-ink-muted">{label}</h2>
      <div className="overflow-hidden rounded-3xl border border-line bg-surface shadow-sm [&>*+*]:border-t [&>*+*]:border-line">
        {children}
      </div>
    </section>
  );
}

function RowIcon({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="grid size-10 shrink-0 place-items-center rounded-full bg-primary-soft text-primary-text">
      <Icon aria-hidden="true" className="size-6" strokeWidth={1.75} />
    </span>
  );
}

type RowProps = {
  icon: LucideIcon;
  title: string;
  value?: string;
} & (
  | { to: "/help" | "/consent/revoke"; params?: undefined }
  | { to: "/profile/$setting"; params: { setting: SettingName } }
);

/** Строка настройки (компонент «Пациент/Строка настройки»): значок в круге, название, значение, стрелка. */
function Row({ icon, title, value, ...link }: RowProps) {
  return (
    <Link
      {...link}
      className="flex min-h-[4.25rem] items-center gap-3.5 px-4 py-3.5 focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-primary"
    >
      <RowIcon icon={icon} />
      <span className="min-w-0 flex-1">
        <span className="block text-[17px] leading-6 text-ink">{title}</span>
        {value && (
          <span className="mt-0.5 block truncate text-[15px] leading-5 text-ink-muted">
            {value}
          </span>
        )}
      </span>
      <span aria-hidden="true" className="shrink-0 text-xl text-ink-muted">
        ›
      </span>
    </Link>
  );
}

type SettingName = "name" | "reminder" | "mood" | "story" | "pills" | "text" | "consent";

function SettingRow({
  setting,
  ...rest
}: {
  setting: Exclude<SettingName, "consent">;
  icon: LucideIcon;
  title: string;
  value: string;
}) {
  return <Row to="/profile/$setting" params={{ setting }} {...rest} />;
}
