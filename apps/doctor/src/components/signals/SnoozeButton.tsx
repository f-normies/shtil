import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { lastSnoozeDay, moscowToday, snoozeChoices } from "../../card/alerts";
import { detailOf } from "../../card/errors";
import { refreshAlerts } from "../../card/queries";
import { type Placement, placePopover, snoozeDateError } from "../../card/snooze";

type Snooze = Schemas["SnoozeOptionsOut"];

/** Что откладываем: один сигнал или все сигналы маршрутизации пациента разом (#35). */
export type SnoozeTarget = { alertId: string } | { patientId: string; alertIds: string[] };

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * «Отложить до…» (кадр 11б): поповер с вариантами 3 дня, неделя, 2 недели и «Другая дата…».
 * Даты — с завтра до предела `max_days`. Отложить можно только «Желательно».
 *
 * Поповер лежит в верхнем слое браузера (`popover="manual"`) и позиционируется от кнопки:
 * внутри прокручиваемого листа направления его иначе обрезает нижний край листа.
 * Esc и «Отмена» закрывают поповер и возвращают фокус на кнопку.
 *
 * `onDone` — после сохранения (лист направления закрывается). `onOpenChange` — поповер
 * открыт: лист не закрывается по Esc, пока открыт поповер.
 */
export function SnoozeButton({
  target,
  snooze,
  onDone,
  onOpenChange,
}: {
  target: SnoozeTarget;
  snooze: Snooze;
  onDone?: () => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const trigger = useRef<HTMLButtonElement | null>(null);
  const panel = useRef<HTMLFormElement>(null);
  // «Сегодня» — по Москве, как считает сервер: у врача в другом поясе даты не разойдутся.
  const today = moscowToday(new Date());
  const choices = snoozeChoices(today, snooze.presets_days);
  const [open, setOpenState] = useState(false);
  const [place, setPlace] = useState<Placement | null>(null);
  const setOpen = (value: boolean) => {
    setOpenState(value);
    onOpenChange?.(value);
  };
  const dismiss = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const [picked, setPicked] = useState(choices[1]?.date ?? choices[0]?.date ?? "");
  const [other, setOther] = useState(false);
  const one = $api.useMutation("post", "/api/v1/alerts/{alert_id}/snooze", {
    onSuccess: () => {
      setOpen(false);
      onDone?.();
    },
    onSettled: () => refreshAlerts(queryClient),
  });
  const all = $api.useMutation("post", "/api/v1/patients/{patient_id}/referral", {
    onSuccess: () => {
      setOpen(false);
      onDone?.();
    },
    onSettled: () => refreshAlerts(queryClient),
  });
  const mutation = "alertId" in target ? one : all;
  const submit = (until: string) =>
    "alertId" in target
      ? one.mutate({ params: { path: { alert_id: target.alertId } }, body: { until } })
      : all.mutate({
          params: { path: { patient_id: target.patientId } },
          body: { decision: "snooze", alert_ids: target.alertIds, until },
        });
  const detail = detailOf(mutation.error);
  const dateError = snoozeDateError(picked, today, snooze.max_days);
  const valid = ISO_DAY.test(picked) && dateError === null;
  const label = ISO_DAY.test(picked)
    ? new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }).format(
        new Date(`${picked}T00:00:00Z`),
      )
    : "…";

  // Открытие: поповер в верхний слой, фокус на выбранном варианте (радиогруппа, ↑↓).
  useLayoutEffect(() => {
    const el = panel.current;
    if (!open || !el) {
      return;
    }
    el.showPopover?.();
    el.querySelector<HTMLInputElement>("input[type=radio]:checked")?.focus({ preventScroll: true });
  }, [open]);

  // Положение — от кнопки; пересчёт при прокрутке, изменении окна и высоты поповера
  // (поле «Другая дата…», ошибка даты, ошибка сохранения).
  useLayoutEffect(() => {
    const el = panel.current;
    const anchor = trigger.current;
    if (!open || !el || !anchor) {
      return;
    }
    const measure = () => {
      const a = anchor.getBoundingClientRect();
      const p = el.getBoundingClientRect();
      const viewport = { width: window.innerWidth, height: window.innerHeight };
      setPlace(placePopover(a, { width: p.width, height: p.height }, viewport));
    };
    measure();
    const grown = new ResizeObserver(measure);
    grown.observe(el);
    window.addEventListener("resize", measure);
    document.addEventListener("scroll", measure, true);
    return () => {
      grown.disconnect();
      window.removeEventListener("resize", measure);
      document.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: Esc из кнопки и из поповера закрывает только поповер
    <div
      onKeyDown={(e) => {
        // Esc закрывает верхний слой (аннотация кадра 11). Отменённый keydown не даёт
        // браузеру послать `cancel` листу направления.
        if (e.key === "Escape" && open) {
          e.preventDefault();
          e.stopPropagation();
          dismiss();
        }
      }}
    >
      <Button
        variant="secondary"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? `${id}-popover` : undefined}
        onClick={(e) => {
          trigger.current = e.currentTarget;
          setOpen(!open);
        }}
      >
        Отложить до…
      </Button>
      {open && (
        <form
          ref={panel}
          id={`${id}-popover`}
          popover="manual"
          role="dialog"
          aria-labelledby={`${id}-title`}
          className="fixed inset-auto m-0 flex w-80 flex-col gap-3 overflow-visible rounded-xl border border-line bg-surface p-4 text-ink shadow-lg"
          style={place ? { top: place.top, left: place.left } : { visibility: "hidden" }}
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) {
              submit(picked);
            }
          }}
        >
          <h3 id={`${id}-title`} className="font-semibold">
            Отложить сигнал до
          </h3>
          <div
            role="radiogroup"
            aria-labelledby={`${id}-term`}
            aria-describedby={`${id}-hint`}
            className="flex flex-col gap-2 text-sm"
          >
            <span id={`${id}-term`} className="sr-only">
              Срок
            </span>
            {choices.map((c) => (
              <label key={c.date} className="flex items-center gap-2">
                <input
                  type="radio"
                  name={`${id}-until`}
                  className="accent-primary"
                  checked={!other && picked === c.date}
                  onChange={() => {
                    setOther(false);
                    setPicked(c.date);
                  }}
                />
                {c.label}
              </label>
            ))}
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`${id}-until`}
                className="accent-primary"
                checked={other}
                onChange={() => setOther(true)}
              />
              Другая дата…
            </label>
            {other && (
              <>
                <input
                  type="date"
                  aria-label="Дата возврата сигнала"
                  aria-invalid={dateError !== null}
                  aria-describedby={`${id}-date-error`}
                  className="rounded-md border border-line px-2 py-1 aria-[invalid=true]:border-danger"
                  min={snoozeChoices(today, [1])[0]?.date}
                  max={lastSnoozeDay(today, snooze.max_days)}
                  value={picked}
                  onChange={(e) => setPicked(e.target.value)}
                />
                <p id={`${id}-date-error`} role="status" className="text-sm text-danger">
                  {dateError ?? ""}
                </p>
              </>
            )}
          </div>
          <p id={`${id}-hint`} className="text-xs text-ink-muted">
            {valid ? `Сигнал вернётся ${label}. ` : "Выберите дату. "}
            Если уровень поднимется до «Обязательно», сигнал придёт сразу.
          </p>
          {mutation.isError && (
            <Alert tone="error">
              {typeof detail === "string" ? detail : "Не удалось отложить сигнал."}
            </Alert>
          )}
          <div className="flex items-center justify-end gap-3">
            <button type="button" className="text-sm text-primary" onClick={dismiss}>
              Отмена
            </button>
            <Button type="submit" disabled={mutation.isPending || !valid}>
              Отложить до {label}
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
