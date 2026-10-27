import type { ReactNode } from "react";
import { Brand, type BrandPersona } from "./Brand";

export function Panel({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-line bg-surface p-5 sm:p-6 ${className}`.trim()}>
      {title && <h2 className="mb-4 text-lg font-semibold text-ink">{title}</h2>}
      {children}
    </section>
  );
}

/**
 * Шапка приложения: название продукта и, справа, действия пользователя.
 * `wide` — для экранов врача шириной до 1280 px (карточка пациента, #13).
 * `persona` обязателен: знак врача на экране пациента молча испортил бы бренд.
 */
export function AppHeader({
  product = "Штиль",
  persona,
  actions,
  wide = false,
}: {
  product?: string;
  persona: BrandPersona;
  actions?: ReactNode;
  wide?: boolean;
}) {
  return (
    <header className="border-b border-line bg-surface">
      <div
        className={`mx-auto flex ${wide ? "max-w-7xl" : "max-w-5xl"} items-center justify-between gap-4 px-4 py-3`}
      >
        <Brand persona={persona} product={product} />
        {actions}
      </div>
    </header>
  );
}
