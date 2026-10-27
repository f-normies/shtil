import { $api, homePathFor, type Schemas } from "@compass/api-client";
import { Alert, AppHeader, Button, Checkbox, Panel } from "@compass/ui";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { useId, useState } from "react";
import { guardPatientPreferences } from "../preferences/guards";
import { useApplyPatientPreferences, usePatientPreferences } from "../preferences/hooks";
import { resolvePatientSession } from "../session";
import {
  errorStatus,
  lastDayIso,
  moscowDayIso,
  regimenText,
  reportErrorText,
  toggleReaction,
} from "../therapy/therapy";

type ScheduleItem = Schemas["ScheduleItem"];

const DATE = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" });
const formatDay = (iso: string) => DATE.format(new Date(`${iso.slice(0, 10)}T00:00:00`));

export const Route = createFileRoute("/therapy")({
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
    await guardPatientPreferences(context.queryClient, session);
    return { session };
  },
  component: TherapyPage,
});

/** «Моя терапия» (спека #29, раздел 7): действующая схема и отметка нежелательной реакции. */
function TherapyPage() {
  const { session } = Route.useRouteContext();
  const preferences = usePatientPreferences(session);
  useApplyPatientPreferences(preferences.data);
  const online = session.status === "online";
  const schedule = $api.useQuery("get", "/api/v1/me/prescriptions", {}, { enabled: online });

  return (
    <>
      <AppHeader
        persona="patient"
        product="Штиль"
        actions={
          <div className="flex items-center gap-3">
            <Link className="text-sm underline" to="/profile">
              Профиль
            </Link>
            <Link className="text-sm underline" to="/">
              На главную
            </Link>
          </div>
        }
      />
      <main className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-6">
        <h1 className="text-xl font-semibold">Моя терапия</h1>
        {!online && (
          <Alert tone="error">Схема приёма и отметка реакции доступны только при связи.</Alert>
        )}
        {schedule.isPending && online && (
          <p className="text-sm text-ink-muted">Загружаем назначения…</p>
        )}
        {schedule.isError && (
          <Alert tone="error">Не удалось загрузить назначения. Попробуйте ещё раз позже.</Alert>
        )}
        {schedule.data?.length === 0 && (
          <p className="text-sm text-ink-muted">Действующих назначений нет.</p>
        )}
        {schedule.data?.map((item) => (
          <Prescription key={item.prescription_id} item={item} />
        ))}
      </main>
    </>
  );
}

function Prescription({ item }: { item: ScheduleItem }) {
  return (
    <Panel title={item.trade_name}>
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-ink">{regimenText(item)}</p>
        <p className="text-ink-muted">
          С {formatDay(moscowDayIso(item.started_at))}
          {item.ends_on && ` по ${formatDay(lastDayIso(item.ends_on))} включительно`}
        </p>
        {item.instructions.length > 0 && (
          <ul className="list-disc pl-5 text-ink">
            {item.instructions.map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
        )}
        {(item.reactions ?? []).length > 0 && <ReactionForm item={item} />}
      </div>
    </Panel>
  );
}

function ReactionForm({ item }: { item: ScheduleItem }) {
  const noteId = useId();
  const reactions = item.reactions ?? [];
  const order = reactions.map((r) => r.id);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const report = $api.useMutation("post", "/api/v1/me/prescriptions/{prescription_id}/reactions", {
    onSuccess: () => {
      setSelected([]);
      setNote("");
      setOpen(false);
    },
  });

  if (!open) {
    return (
      <div className="flex flex-col gap-2">
        {report.isSuccess && (
          <Alert tone="success">
            Сообщение отправлено лечащему врачу. Если состояние быстро ухудшается, звоните 103 или
            112.
          </Alert>
        )}
        <Button
          variant="secondary"
          className="self-start"
          onClick={() => {
            report.reset();
            setOpen(true);
          }}
        >
          Сообщить о нежелательной реакции
        </Button>
      </div>
    );
  }
  return (
    <form
      className="flex flex-col gap-3 border-t border-line pt-3"
      onSubmit={(e) => {
        e.preventDefault();
        report.mutate({
          params: { path: { prescription_id: item.prescription_id } },
          body: { reactions: selected, note: note.trim() || null },
        });
      }}
    >
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 font-medium text-ink">Что вы заметили?</legend>
        {reactions.map((r) => (
          <Checkbox
            key={r.id}
            checked={selected.includes(r.id)}
            disabled={report.isPending}
            onChange={() => setSelected(toggleReaction(selected, r.id, order))}
          >
            {r.title}
          </Checkbox>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={noteId} className="font-medium text-ink">
          Что ещё хотите сообщить (необязательно)
        </label>
        <textarea
          id={noteId}
          aria-describedby={`${noteId}-hint`}
          className="min-h-20 rounded-md border border-line bg-surface px-3 py-2 text-base text-ink"
          maxLength={2000}
          value={note}
          disabled={report.isPending}
          onChange={(e) => setNote(e.target.value)}
        />
        <p id={`${noteId}-hint`} className="text-ink-muted">
          Подробно о самочувствии — в дневнике.
        </p>
      </div>
      <p className="text-ink">
        Это не экстренная связь: врач прочитает сообщение не сразу. Если состояние быстро
        ухудшается, звоните 103 или 112.
      </p>
      {report.isError && <Alert tone="error">{reportErrorText(errorStatus(report.error))}</Alert>}
      <div className="flex flex-col-reverse gap-2 sm:flex-row">
        <Button
          type="button"
          variant="secondary"
          disabled={report.isPending}
          onClick={() => setOpen(false)}
        >
          Отмена
        </Button>
        <Button type="submit" disabled={report.isPending || selected.length === 0}>
          Отправить врачу
        </Button>
      </div>
    </form>
  );
}
