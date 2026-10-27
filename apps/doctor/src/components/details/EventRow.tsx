import type { ReactNode } from "react";

/**
 * Строка события в секции («Врач v4/Строка события»): значок, событие, подробности, время.
 * Ставится в список `EventList`; ссылку или кнопку вокруг строки задаёт вызывающий.
 */
export function EventRow({
  icon,
  title,
  detail,
  time,
}: {
  icon: ReactNode;
  title: ReactNode;
  detail?: ReactNode;
  time?: ReactNode;
}) {
  return (
    <span className="flex gap-3 px-4 py-3">
      <span className="flex size-5 shrink-0 items-center justify-center text-ink-muted">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="text-[15px] leading-[22px] text-ink">{title}</span>
        {detail && <span className="text-[13px] leading-[18px] text-ink-muted">{detail}</span>}
      </span>
      {time && <span className="shrink-0 text-xs leading-4 text-ink-muted">{time}</span>}
    </span>
  );
}

/** Строки секции с разделителями: у последней разделителя нет. */
export function EventList({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <ul aria-label={label} className="flex flex-col [&>li+li]:border-t [&>li+li]:border-separator">
      {children}
    </ul>
  );
}

/** Строка, которая ведёт дальше: наведение — surface-sunken, клик по всей строке. */
export const ROW_LINK =
  "block rounded-xl hover:bg-surface-sunken focus-visible:outline-2 focus-visible:outline-focus";
