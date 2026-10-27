import type { ReactNode } from "react";

/** Волосяная линия между строками группы с отступом под значок, как в кадре. */
export const DIVIDER =
  "relative [&+&]:before:absolute [&+&]:before:top-0 [&+&]:before:right-0 [&+&]:before:left-12 [&+&]:before:h-px [&+&]:before:bg-line";

/**
 * Сгруппированная секция кадра 07в: подпись прописными, справа счётчик, белая группа
 * со скруглением. `proposal` — пунктирная рамка: в группе предложения системы.
 */
export function Group({
  id,
  title,
  aside,
  proposal = false,
  children,
}: {
  id: string;
  title: string;
  aside?: string;
  proposal?: boolean;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <div className="flex items-baseline gap-3 px-4 text-xs leading-4 text-ink-muted">
        <h3 id={id} className="flex-1 font-semibold tracking-[0.4px] uppercase">
          {title}
        </h3>
        {aside && <p>{aside}</p>}
      </div>
      <ul
        className={`overflow-hidden rounded-xl bg-surface ${proposal ? "border border-dashed border-primary-text" : ""}`}
      >
        {children}
      </ul>
    </section>
  );
}

/** Строка без действий: значок, текст и подробности, справа страница или время. */
export function Row({
  icon,
  title,
  subtitle,
  aside,
}: {
  icon: ReactNode;
  title: string;
  subtitle: string;
  aside?: string;
}) {
  return (
    <li className={`flex gap-3 px-4 py-3 ${DIVIDER}`}>
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-[22px] text-ink">{title}</p>
        <p className="text-[13px] leading-[18px] text-ink-muted">{subtitle}</p>
      </div>
      {aside && (
        <span className="shrink-0 text-[13px] leading-[18px] whitespace-nowrap text-ink-muted">
          {aside}
        </span>
      )}
    </li>
  );
}
