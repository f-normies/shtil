import type { ReactNode } from "react";

/**
 * Разметка шапки пациента («Врач v4/Тулбар подробностей», кадры 03 и 05): ФИО своей
 * строкой на всю ширину, ниже — сведения через запятую слева и действия справа. Длинное
 * ФИО переносится на вторую строку и кнопки не теснит. Без хуков: данные и кнопки даёт
 * `PatientTitle`.
 */
export function PatientHeader({
  name,
  about,
  actions,
  large = false,
}: {
  name: string;
  about: string;
  actions?: ReactNode;
  large?: boolean;
}) {
  // В подробностях «Пациентов» и «Сигналов» заголовок экрана — у списка, здесь второй уровень.
  const Heading = large ? "h1" : "h2";
  return (
    <div className="flex flex-col gap-1">
      <Heading
        className={`line-clamp-2 font-semibold text-ink ${large ? "text-[26px] leading-8 tracking-[-0.5px]" : "text-2xl"}`}
      >
        {name}
      </Heading>
      {/* Сведениям нужна читаемая ширина: в узкой панели действия уходят на свою строку. */}
      <div
        data-part="about-row"
        className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
      >
        <p className="min-w-[16rem] flex-1 text-[15px] leading-[22px] text-ink-muted">{about}</p>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
