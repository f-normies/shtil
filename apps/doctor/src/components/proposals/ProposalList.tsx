import { CircleCheck, CircleDotDashed, X } from "lucide-react";
import { type KeyboardEvent, useRef } from "react";
import { momentText } from "../../card/documents";
import { type PatientGroup, type QueueItem, rowView } from "../../card/proposalQueue";

/**
 * Список предложений (кадр 04, «Врач v4/Строка списка»): группы по пациентам с числом
 * справа от имени или один список без групп (вкладка карточки, кадр 07б). Строка выбирает
 * предложение, подробности — справа. ↑↓ переходят по предложениям, Enter ставит фокус
 * на основное действие подробностей. В списке одна точка табуляции — выбранная строка.
 */
export function ProposalList({
  groups,
  grouped,
  selectedId,
  leaving,
  onSelect,
  onLeft,
  onEnter,
  now,
}: {
  groups: PatientGroup[];
  grouped: boolean;
  selectedId: string | null;
  leaving: ReadonlySet<string>;
  onSelect: (id: string) => void;
  onLeft: (id: string) => void;
  onEnter: () => void;
  now: Date;
}) {
  const rows = useRef(new Map<string, HTMLButtonElement>());
  const order = groups.flatMap((g) => g.items).filter((i) => !leaving.has(i.id));

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      // Подробности уже открыты: строка выбирается кликом или стрелками.
      onEnter();
      return;
    }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") {
      return;
    }
    const index = order.findIndex((i) => i.id === selectedId);
    const next = order[index + (e.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      e.preventDefault();
      onSelect(next.id);
      rows.current.get(next.id)?.focus();
    }
  };

  const row = (item: QueueItem) => (
    <li key={item.id}>
      <Row
        item={item}
        selected={item.id === selectedId}
        tabbable={item.id === selectedId || (selectedId === null && item === order[0])}
        leaving={leaving.has(item.id)}
        onSelect={() => onSelect(item.id)}
        onLeft={() => onLeft(item.id)}
        register={(element) => {
          if (element) {
            rows.current.set(item.id, element);
          } else {
            rows.current.delete(item.id);
          }
        }}
        now={now}
      />
    </li>
  );

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: стрелки и Enter дополняют обычные кнопки строк.
    <div className="flex flex-col" onKeyDown={onKeyDown}>
      {grouped ? (
        groups.map((g) => (
          <section key={g.patientId} aria-labelledby={`proposals-${g.patientId}`}>
            <h2
              id={`proposals-${g.patientId}`}
              className="flex gap-2 px-3 pt-5 pb-2 text-xs font-semibold tracking-[0.4px] uppercase"
            >
              <span className="min-w-0 flex-1 truncate text-ink-muted">{g.name}</span>
              <span className="text-ink-subtle">
                <span className="sr-only">, предложений: </span>
                {g.items.length}
              </span>
            </h2>
            <ul className="flex flex-col gap-1">{g.items.map(row)}</ul>
          </section>
        ))
      ) : (
        <ul className="flex flex-col gap-1 pt-2">{groups.flatMap((g) => g.items).map(row)}</ul>
      )}
    </div>
  );
}

const STATUS_WORD = {
  proposed: "Предложение системы",
  confirmed: "Подтверждено врачом",
  rejected: "Отклонено",
} as const;

function Row({
  item,
  selected,
  tabbable,
  leaving,
  onSelect,
  onLeft,
  register,
  now,
}: {
  item: QueueItem;
  selected: boolean;
  tabbable: boolean;
  leaving: boolean;
  onSelect: () => void;
  onLeft: () => void;
  register: (element: HTMLButtonElement | null) => void;
  now: Date;
}) {
  const view = rowView(item);
  const proposed = item.status === "proposed";
  // Пунктир у ждущего решения (кадр 04); у выбранной строки его заменяет заливка primary.
  const frame = selected
    ? "border border-primary bg-primary text-primary-ink"
    : proposed
      ? "border border-dashed border-primary-text hover:bg-canvas"
      : "border border-transparent hover:bg-canvas";
  const muted = selected ? "" : "text-ink-muted";
  const subtle = selected ? "" : "text-ink-subtle";

  return (
    <button
      ref={register}
      type="button"
      tabIndex={tabbable ? 0 : -1}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      onAnimationEnd={leaving ? onLeft : undefined}
      className={`flex w-full gap-3 rounded-lg p-3 text-left focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus ${frame} ${leaving ? "pointer-events-none animate-fade-out" : ""}`}
    >
      <StatusGlyph status={item.status} selected={selected} />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="sr-only">{STATUS_WORD[item.status]}:</span>
        <span className="flex items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate text-[15px] leading-5 font-semibold">
            {view.title}
          </span>
          <span className={`shrink-0 text-xs leading-4 ${subtle}`}>
            {momentText(item.created_at, now)}
          </span>
        </span>
        {view.detail && (
          <span className={`line-clamp-2 text-[13px] leading-[18px] ${muted}`}>{view.detail}</span>
        )}
        {view.meta && <span className={`text-xs leading-4 ${subtle}`}>{view.meta}</span>}
        {selected && <span className="sr-only">, выбрано</span>}
      </span>
    </button>
  );
}

/** Значок статуса 20 px: пунктирный круг, галочка или крест; на заливке — цвет текста.
 * Подсказка — слово статуса. */
export function StatusGlyph({
  status,
  selected = false,
}: {
  status: QueueItem["status"];
  selected?: boolean;
}) {
  const className = "size-5";
  const glyph =
    status === "confirmed" ? (
      <CircleCheck aria-hidden className={`${className} ${selected ? "" : "text-success"}`} />
    ) : status === "rejected" ? (
      <X aria-hidden className={`${className} ${selected ? "" : "text-ink-muted"}`} />
    ) : (
      <CircleDotDashed
        aria-hidden
        className={`${className} ${selected ? "" : "text-primary-text"}`}
      />
    );
  // Слово статуса — подсказкой: в строке его видит только экранная читалка (#36, правило 3).
  return (
    <span title={STATUS_WORD[status]} className="mt-0.5 flex size-5 shrink-0">
      {glyph}
    </span>
  );
}
