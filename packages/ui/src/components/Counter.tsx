import type { HTMLAttributes, ReactNode } from "react";

export type CounterTone = "neutral" | "primary" | "required" | "advised";

export type CounterProps = Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  value: ReactNode;
  tone?: CounterTone;
};

/** Числовой счётчик без фона-пилюли, как в навигации врача. */
export function Counter({ value, tone = "neutral", className = "", ...props }: CounterProps) {
  return (
    <span {...props} className={`ui-counter ${className}`.trim()} data-tone={tone}>
      {value}
    </span>
  );
}
