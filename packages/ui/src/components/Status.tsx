import type { HTMLAttributes } from "react";

export type StatusKind = "required" | "advised" | "proposed" | "confirmed";

const STATUS_LABELS: Record<StatusKind, string> = {
  required: "Обязательно",
  advised: "Желательно",
  proposed: "Предложение",
  confirmed: "Подтверждено врачом",
};

const STATUS_ICONS: Record<StatusKind, string> = {
  required: new URL("../assets/signal-obyazatelno.svg", import.meta.url).href,
  advised: new URL("../assets/signal-zhelatelno.svg", import.meta.url).href,
  proposed: new URL("../assets/signal-predlozhenie-instance.svg", import.meta.url).href,
  confirmed: new URL("../assets/signal-podtverzhdeno.svg", import.meta.url).href,
};
const SELECTED_REQUIRED_ICON = new URL("../assets/signal-obyazatelno-selected.svg", import.meta.url)
  .href;

export type StatusIconProps = {
  status: StatusKind;
  label?: string;
  decorative?: boolean;
  accent?: boolean;
  className?: string;
};

/** Знак категории сигнала из готового локального SVG. Подпись — и для читалки, и подсказкой. */
export function StatusIcon({
  status,
  label = STATUS_LABELS[status],
  decorative = false,
  accent = false,
  className = "",
}: StatusIconProps) {
  return (
    <img
      className={`ui-status-icon ${className}`.trim()}
      src={status === "required" && accent ? SELECTED_REQUIRED_ICON : STATUS_ICONS[status]}
      alt={decorative ? "" : label}
      title={decorative ? undefined : label}
      aria-hidden={decorative || undefined}
      data-status={status}
      data-accent={accent || undefined}
      width="20"
      height="20"
    />
  );
}

export type StatusBadgeProps = Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  status: StatusKind;
  label?: string;
  iconOnly?: boolean;
  selected?: boolean;
};

/** Знак и слово передают смысл статуса независимо от цвета. */
export function StatusBadge({
  status,
  label = STATUS_LABELS[status],
  iconOnly = false,
  selected = false,
  className = "",
  "aria-label": ariaLabel,
  ...props
}: StatusBadgeProps) {
  return (
    <span
      {...props}
      className={`ui-status-badge ${className}`.trim()}
      data-status={status}
      data-selected={selected || undefined}
      data-icon-only={iconOnly || undefined}
      role="img"
      aria-label={ariaLabel ?? label}
    >
      <StatusIcon status={status} decorative accent={selected} />
      {!iconOnly && <span className="ui-status-badge__label">{label}</span>}
    </span>
  );
}
