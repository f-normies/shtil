import type { Schemas } from "@compass/api-client";
import { deltaText, longDay, type PointSlot, type Slot } from "../../card/track";

const SENT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });

/**
 * Поповер точки графика (кадры 09 и 09в): дата, балл, градация, изменение к прошлому
 * измерению, ответы по пунктам столбиками и кто заполнил. У пропуска — дата и «Отметки
 * не было». Ответы — с текстом пункта и варианта из определения шкалы.
 */
export function TrackPopover({
  slot,
  previous,
  missed,
  name,
  max,
  definition,
}: {
  slot: Slot;
  previous: PointSlot | undefined;
  /** Пропуски между прошлым измерением и этим. */
  missed: readonly string[];
  name: string;
  max: number | null;
  definition: Schemas["ScaleDefinitionOut"] | undefined;
}) {
  if (slot.kind === "gap") {
    return (
      <div className="flex flex-col gap-1">
        <p className="text-xs text-ink-muted">{longDay(slot.at)}</p>
        <p className="text-[15px] font-semibold text-ink">Отметки не было</p>
      </div>
    );
  }
  const r = slot.result;
  const items = definition?.scale.items ?? [];
  // Дни сравниваются в поясе браузера: по UTC ответ после полуночи был бы «отправлен позже».
  const sent = new Date(r.received_at).toDateString() !== new Date(r.completed_at).toDateString();
  return (
    <div className="flex flex-col gap-2">
      <div>
        <p className="text-xs text-ink-muted">{longDay(slot.at)}</p>
        <p className="text-[15px] font-semibold text-ink">
          {name}: {slot.value}
          {max !== null && ` из ${max}`}
        </p>
        <p className="text-sm text-ink-muted">{slot.band}</p>
        <p className="text-sm text-ink-muted">
          {previous ? deltaText(previous, slot, missed) : "Первое измерение"}
        </p>
      </div>
      {items.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-separator pt-2">
          <p className="text-xs text-ink-muted">Ответы по пунктам</p>
          <ol className="flex gap-3">
            {items.map((item, i) => {
              const value = r.answers[item.id];
              const top = Math.max(1, ...item.options.map((o) => o.value));
              const answer = item.options.find((o) => o.value === value)?.text;
              const share = typeof value === "number" ? value / top : 0;
              return (
                <li
                  key={item.id}
                  title={`${i + 1}. ${item.text}${answer ? `: ${answer}` : ""}`}
                  className="flex flex-col items-center gap-1 text-xs text-ink-muted"
                >
                  {/* Текст пункта и ответа — для экранных читалок; мышью — в подсказке. */}
                  <span className="sr-only">
                    {i + 1}. {item.text}: {answer ?? "нет ответа"}
                  </span>
                  <span aria-hidden className="text-ink">
                    {typeof value === "number" ? value : "—"}
                  </span>
                  <span aria-hidden className="relative h-9 w-2 rounded-sm bg-surface-sunken">
                    <span
                      className="absolute inset-x-0 bottom-0 rounded-sm bg-primary"
                      style={{ height: `${share * 100}%` }}
                    />
                  </span>
                  <span aria-hidden>{i + 1}</span>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      <p className="border-t border-separator pt-2 text-xs text-ink-muted">
        {r.recorded_by ? "Заполнено врачом на приёме" : "Заполнено пациентом"}
        {sent && `, отправлено ${SENT.format(new Date(r.received_at))}`}
      </p>
    </div>
  );
}
