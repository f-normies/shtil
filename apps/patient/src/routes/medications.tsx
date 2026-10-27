import { $api, homePathFor, type Schemas } from "@compass/api-client";
import { Alert, AppHeader, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, redirect, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  closingMarks,
  doseLine,
  isEvening,
  moscowDay,
  nextStatus,
  withMarks,
} from "../medications/doses";
import { guardPatientPreferences } from "../preferences/guards";
import { resolvePatientSession } from "../session";

type Dose = Schemas["DoseOut"];
type Mark = Schemas["MarkIn"];

const DOSES = "/api/v1/me/doses";
const SEND_ERROR = "Не удалось отправить. Проверьте связь и попробуйте ещё раз.";
const dosesQuery = (day: string) => ({ params: { query: { day } } });

export const Route = createFileRoute("/medications")({
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
  component: MedicationsPage,
});

/**
 * «Лекарства на сегодня» (спека #34, раздел 7; кадр «Пациент · 16»). Касание квадрата
 * сразу сохраняет отметку. С 17:00 по Москве «Готово» сохраняет как пропущенные пустые
 * приёмы, время которых пришло; до вечера экран просто закрывается. Офлайн-очередь — в
 * потоке отметки (#31).
 */
function MedicationsPage() {
  const { session } = Route.useRouteContext();
  const online = session.status === "online";
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [clock, setClock] = useState(() => new Date());
  const day = moscowDay(clock);
  // Открытая вкладка переходит на новый московский день; перед действием время и дозы
  // дополнительно перечитываются, в том числе после сна устройства.
  useEffect(() => {
    const timer = setInterval(() => setClock(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);
  const today = $api.useQuery("get", DOSES, dosesQuery(day), { enabled: online });
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState(false);
  // Отметка видна сразу, в том же касании; ответ сервера заменяет её итоговой,
  // ошибка возвращает прежнее состояние.
  const [pending, setPending] = useState<Mark[] | null>(null);
  const mark = $api.useMutation("put", DOSES, {
    onSuccess: (data) =>
      queryClient.setQueryData(
        $api.queryOptions("get", DOSES, dosesQuery(data.day)).queryKey,
        data,
      ),
    // Ошибка: отметка могла дойти до сервера. Экран перечитывает дозы дня.
    onError: () =>
      void queryClient.invalidateQueries({
        queryKey: ["get", DOSES],
      }),
    onSettled: () => setPending(null),
  });
  const send = (marks: Mark[], after?: () => void) => {
    setPending(marks);
    mark.mutate({ body: { marks } }, { onSuccess: after });
  };
  const evening = isEvening(clock);
  const shown = today.data && pending ? withMarks(today.data, pending, new Date()) : today.data;
  const doses = shown?.doses ?? [];
  const closing = today.data ? closingMarks(today.data.day, doses) : [];

  const prepare = async (action: (data: Schemas["DayDosesOut"], at: Date) => void) => {
    setRefreshing(true);
    setRefreshError(false);
    try {
      const at = new Date();
      setClock(at);
      const requestedDay = moscowDay(at);
      const data = await queryClient.fetchQuery({
        ...$api.queryOptions("get", DOSES, dosesQuery(requestedDay)),
        staleTime: 0,
      });
      const receivedAt = new Date();
      setClock(receivedAt);
      if (data.day !== moscowDay(receivedAt)) throw new Error("Dose day changed while refreshing");
      action(data, receivedAt);
    } catch {
      setRefreshError(true);
      setPending(null);
    } finally {
      setRefreshing(false);
    }
  };
  const toggle = (dose: Dose) => {
    const shownDay = today.data?.day;
    if (!shownDay) return;
    const status = nextStatus(dose.status);
    // Намерение касания видно сразу, пока перед записью уточняются день и схема.
    setPending([{ prescription_id: dose.prescription_id, day: shownDay, slot: dose.slot, status }]);
    return prepare((data) => {
      if (data.day !== shownDay) {
        setPending(null);
        return;
      }
      const current = data.doses.find(
        (d) => d.prescription_id === dose.prescription_id && d.slot === dose.slot,
      );
      if (!current) {
        setPending(null);
        return;
      }
      send([
        {
          prescription_id: current.prescription_id,
          day: data.day,
          slot: current.slot,
          status,
        },
      ]);
    });
  };
  const finish = () =>
    prepare((data, at) => {
      const marks = isEvening(at) ? closingMarks(data.day, data.doses) : [];
      if (marks.length === 0) {
        void navigate({ to: "/" });
        return;
      }
      send(marks, () => void navigate({ to: "/" }));
    });
  const busy = refreshing || mark.isPending || !online;

  return (
    <>
      <AppHeader
        persona="patient"
        product="Штиль"
        actions={
          <Link className="text-sm underline" to="/">
            На главную
          </Link>
        }
      />
      <main className="mx-auto flex min-h-[calc(100dvh-64px)] max-w-lg flex-col gap-4 px-4 py-6">
        <div className="flex flex-col gap-2">
          <h1 className="text-[28px] leading-8 font-semibold text-ink">Лекарства на сегодня</h1>
          <p className="text-[17px] leading-6 text-ink-muted">
            Отметьте, что уже приняли. Если приём пропущен, оставьте пустым.
          </p>
        </div>
        {!online && <Alert tone="error">Отметить приём можно только при связи.</Alert>}
        {today.isPending && online && (
          <p className="text-sm text-ink-muted">Загружаем лекарства…</p>
        )}
        {today.isError && (
          <Alert tone="error">Не удалось загрузить лекарства. Попробуйте ещё раз позже.</Alert>
        )}
        {today.data && doses.length === 0 && (
          <p className="text-[17px] text-ink">На сегодня приёмов нет.</p>
        )}
        {doses.length > 0 && (
          <ul className="flex flex-col rounded-2xl bg-surface shadow-sm [&>li+li]:border-t [&>li+li]:border-line">
            {doses.map((d) => (
              <li key={`${d.prescription_id}:${d.slot}`}>
                <DoseRow dose={d} busy={busy} onToggle={() => void toggle(d)} />
              </li>
            ))}
          </ul>
        )}
        {(mark.isError || refreshError) && <Alert tone="error">{SEND_ERROR}</Alert>}
        <div className="mt-auto flex flex-col gap-2 pt-4">
          {evening && closing.length > 0 && (
            <p className="text-center text-sm text-ink-muted">
              Пустые приёмы, время которых прошло, сохранятся как пропущенные
            </p>
          )}
          <Button className="w-full" disabled={busy} onClick={() => void finish()}>
            {evening ? "Готово" : "На главную"}
          </Button>
        </div>
      </main>
    </>
  );
}

/** Строка дозы: пиктограмма, препарат и приём, квадрат отметки 44 px. */
function DoseRow({ dose, busy, onToggle }: { dose: Dose; busy: boolean; onToggle: () => void }) {
  const taken = dose.status === "taken";
  const title = `${dose.trade_name} ${dose.dose_mg} мг`;
  return (
    <div className="flex min-h-[72px] items-center gap-3 px-4 py-3">
      <span
        aria-hidden
        className="flex size-11 shrink-0 items-center justify-center rounded-full bg-surface-sunken text-primary"
      >
        {/* Таблетка: круг с риской, как на кадре 16. */}
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          className="size-6"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.75}
        >
          <circle cx="12" cy="12" r="8" />
          <path d="M8 12h8" strokeLinecap="round" />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[17px] leading-6 text-ink">{title}</p>
        <p className={`text-[15px] leading-5 ${taken ? "text-success" : "text-ink-muted"}`}>
          {doseLine(dose)}
        </p>
      </div>
      <label className="relative flex size-11 shrink-0 cursor-pointer items-center justify-center rounded-lg has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-primary">
        <input
          type="checkbox"
          className="absolute inset-0 m-0 cursor-pointer opacity-0"
          checked={taken}
          aria-label={`${title}, ${doseLine(dose).toLowerCase()}`}
          disabled={busy}
          onChange={onToggle}
        />
        <span
          aria-hidden
          className={`pointer-events-none flex size-[26px] items-center justify-center rounded-[6px] border-2 transition-colors duration-200 motion-reduce:transition-none ${taken ? "border-primary bg-primary text-surface" : "border-primary bg-surface"}`}
        >
          <svg
            aria-hidden
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={3}
            className={`size-5 transition-transform duration-200 motion-reduce:transition-none ${taken ? "scale-100" : "scale-0"}`}
          >
            <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </label>
    </div>
  );
}
