import type { Schemas } from "@compass/api-client";
import { Alert, AppHeader, Button } from "@compass/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { PatientPreferencesError, patientPreferencesQueryKey, savePatientPreferences } from "./api";
import { describeAcceptedConsent } from "./consent-label";
import type { AccessiblePatientSession } from "./hooks";
import {
  patientOwnerId,
  useAcceptedConsent,
  useApplyPatientPreferences,
  usePatientPreferences,
  usePatientPrescriptions,
} from "./hooks";
import type { EditablePreferences } from "./onboarding-flow";
import { PillAppearanceSection } from "./PillAppearanceEditor";
import { PreferenceField } from "./PreferenceFields";

export type ProfileSetting = "name" | "reminder" | "mood" | "story" | "pills" | "text" | "consent";

const fieldBySetting: Partial<Record<ProfileSetting, keyof EditablePreferences>> = {
  name: "display_name",
  reminder: "reminder_time",
  mood: "mood_palette",
  story: "story_mode",
  text: "text_size",
};

const titleBySetting: Record<ProfileSetting, string> = {
  name: "Обращение",
  reminder: "Время напоминания",
  mood: "Цвет настроения",
  story: "Рассказ о дне",
  pills: "Вид таблеток",
  text: "Размер текста",
  consent: "Принятая редакция согласия",
};

function editableValues(preferences: Schemas["PatientPreferencesOut"]): EditablePreferences {
  return {
    display_name: preferences.display_name,
    reminder_time: preferences.reminder_time,
    mood_palette: preferences.mood_palette,
    story_mode: preferences.story_mode,
    text_size: preferences.text_size,
  };
}

export function ProfileSettingPage({
  session,
  setting,
}: {
  session: AccessiblePatientSession;
  setting: ProfileSetting;
}) {
  const ownerId = patientOwnerId(session);
  const preferences = usePatientPreferences(session);
  const prescriptions = usePatientPrescriptions(session, setting === "pills");
  const consent = useAcceptedConsent(session, setting === "consent");
  useApplyPatientPreferences(fieldBySetting[setting] ? undefined : preferences.data);

  return (
    <>
      <AppHeader
        persona="patient"
        product="Штиль"
        actions={
          <Link className="text-sm font-medium underline" to="/profile">
            В профиль
          </Link>
        }
      />
      <main className="mx-auto flex max-w-lg flex-col gap-5 px-4 py-6 sm:py-8">
        <div>
          <Link className="text-sm font-medium text-primary underline" to="/profile">
            ← Профиль
          </Link>
          <h1 className="mt-3 text-2xl font-semibold">{titleBySetting[setting]}</h1>
        </div>
        {session.status === "offline" && (
          <div
            role="status"
            className="rounded-xl border border-line bg-surface p-3 text-sm text-ink-muted"
          >
            Офлайн-режим. Настройки доступны для чтения. Для сохранения нужно подключение.
          </div>
        )}
        {setting === "consent" ? (
          <ConsentView session={session} query={consent} />
        ) : setting === "pills" ? (
          <section className="flex flex-col gap-3">
            {prescriptions.isPending ? (
              <p role="status" className="text-sm text-ink-muted">
                Загружаем назначения…
              </p>
            ) : prescriptions.isError ? (
              <>
                <Alert tone="error">
                  Не удалось загрузить назначения. Ошибка не означает, что назначений нет.
                </Alert>
                <Button
                  variant="secondary"
                  onClick={() => void prescriptions.refetch()}
                  disabled={prescriptions.isFetching}
                >
                  Повторить
                </Button>
              </>
            ) : (
              <PillAppearanceSection
                items={prescriptions.data ?? []}
                ownerId={ownerId}
                online={session.status === "online"}
              />
            )}
          </section>
        ) : (
          <PreferenceEditor
            field={fieldBySetting[setting]!}
            session={session}
            ownerId={ownerId}
            preferences={preferences}
          />
        )}
      </main>
    </>
  );
}

function PreferenceEditor({
  field,
  session,
  ownerId,
  preferences,
}: {
  field: keyof EditablePreferences;
  session: AccessiblePatientSession;
  ownerId: string;
  preferences: ReturnType<typeof usePatientPreferences>;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<EditablePreferences | null>(
    preferences.data ? editableValues(preferences.data) : null,
  );
  const [saved, setSaved] = useState(false);
  const mutation = useMutation({
    mutationFn: (values: Partial<EditablePreferences>) =>
      savePatientPreferences(ownerId, preferences.data?.revision ?? 0, values),
    onSuccess: (result) => {
      queryClient.setQueryData(patientPreferencesQueryKey(ownerId), result);
      setDraft(editableValues(result));
      setSaved(true);
    },
  });
  useEffect(() => {
    if (!draft && preferences.data) setDraft(editableValues(preferences.data));
  }, [draft, preferences.data]);
  useApplyPatientPreferences(
    preferences.data && draft ? { ...preferences.data, ...draft } : preferences.data,
  );

  if (preferences.isPending || !draft || !preferences.data) {
    return preferences.isError ? (
      <div className="flex flex-col gap-3">
        <Alert tone="error">Не удалось загрузить настройки.</Alert>
        <Button
          variant="secondary"
          onClick={() => void preferences.refetch()}
          disabled={preferences.isFetching}
        >
          Повторить
        </Button>
      </div>
    ) : (
      <p role="status" className="text-sm text-ink-muted">
        Загружаем настройки…
      </p>
    );
  }

  const changed = draft[field] !== preferences.data[field];
  const update: Partial<EditablePreferences> = { [field]: draft[field] };
  const conflict =
    mutation.error instanceof PatientPreferencesError && mutation.error.status === 409;
  const setField = (
    changedField: keyof EditablePreferences,
    value: EditablePreferences[keyof EditablePreferences],
  ) => {
    setSaved(false);
    setDraft((current) => (current ? { ...current, [changedField]: value } : current));
  };

  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-4 sm:p-5">
      <fieldset
        disabled={session.status !== "online" || mutation.isPending}
        className="min-w-0 disabled:opacity-90"
      >
        <PreferenceField
          field={field}
          values={draft}
          onChange={(changedField, value) => setField(changedField, value)}
          reminderMode="profile"
          disabled={session.status !== "online" || mutation.isPending}
        />
      </fieldset>
      {mutation.isError && (
        <Alert tone="error">
          {conflict
            ? "Настройки изменились на другом устройстве. Обновите данные и сохраните свой выбор ещё раз."
            : "Не удалось сохранить настройку. Проверьте подключение и повторите."}
        </Alert>
      )}
      {saved && (
        <p role="status" className="text-sm font-medium text-primary">
          Настройка сохранена.
        </p>
      )}
      <div className="flex flex-col gap-2">
        <Button
          onClick={() => mutation.mutate(update)}
          disabled={
            session.status !== "online" ||
            !changed ||
            (field === "display_name" && !draft.display_name.trim()) ||
            mutation.isPending
          }
        >
          {mutation.isPending ? "Сохраняем…" : "Сохранить"}
        </Button>
        {conflict && (
          <Button
            variant="secondary"
            onClick={() => {
              // Свежие данные снимают и сообщение о конфликте: иначе алерт и кнопка остаются.
              mutation.reset();
              void preferences.refetch();
            }}
            disabled={preferences.isFetching}
          >
            Обновить данные
          </Button>
        )}
      </div>
    </div>
  );
}

function ConsentView({
  session,
  query,
}: {
  session: AccessiblePatientSession;
  query: ReturnType<typeof useAcceptedConsent>;
}) {
  if (query.isPending)
    return (
      <p role="status" className="text-sm text-ink-muted">
        Загружаем согласие…
      </p>
    );
  if (query.isError) {
    return (
      <div className="flex flex-col gap-3">
        <Alert tone="error">Не удалось загрузить принятую редакцию.</Alert>
        <Button
          variant="secondary"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          Повторить
        </Button>
      </div>
    );
  }
  if (!query.data) return <p className="text-sm text-ink-muted">Сведения о согласии недоступны.</p>;
  return (
    <section className="flex flex-col gap-4 rounded-2xl border border-line bg-surface p-4 sm:p-5">
      <p className="text-sm text-ink-muted">{describeAcceptedConsent(query.data)}.</p>
      {query.data.text ? (
        <div className="max-h-[65dvh] overflow-auto whitespace-pre-wrap rounded-xl bg-canvas p-4 text-sm leading-6 text-ink">
          {query.data.text}
        </div>
      ) : (
        <p className="rounded-xl bg-canvas p-3 text-sm text-ink-muted">
          Текст принятой редакции недоступен. Новая редакция не подменяет её.
        </p>
      )}
      {session.status === "offline" ? (
        <Button variant="secondary" disabled>
          Отзыв доступен при подключении
        </Button>
      ) : (
        <Link
          to="/consent/revoke"
          className="flex min-h-12 items-center justify-center rounded-xl border border-line bg-surface px-4 text-center font-medium text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          Отозвать согласие
        </Link>
      )}
    </section>
  );
}
