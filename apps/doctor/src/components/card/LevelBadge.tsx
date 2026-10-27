import { CircleArrowRight, OctagonAlert } from "lucide-react";

type Level = "mandatory" | "desirable";

/** Рамка и фон блока по уровню сигнала. */
export const LEVEL_BOX: Record<Level, string> = {
  mandatory: "border-danger bg-danger-soft",
  desirable: "border-warning bg-warning-soft",
};

const BADGE: Record<Level, { className: string; Icon: typeof OctagonAlert; label: string }> = {
  mandatory: { className: "bg-danger text-white", Icon: OctagonAlert, label: "Обязательно" },
  desirable: { className: "bg-warning text-white", Icon: CircleArrowRight, label: "Желательно" },
};

/** Значок уровня по макетам v4: восьмиугольник — «Обязательно», круг со стрелкой — «Желательно». */
export const LEVEL_ICON = { mandatory: OctagonAlert, desirable: CircleArrowRight } as const;

/** Уровень сигнала: разная иконка, текст и цвет — категории различимы без цвета. */
export function LevelBadge({ level }: { level: Level }) {
  const { className, Icon, label } = BADGE[level];
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${className}`}
    >
      <Icon aria-hidden className="size-3.5" />
      {label}
    </span>
  );
}
