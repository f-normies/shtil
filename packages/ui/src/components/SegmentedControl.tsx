import type { HTMLAttributes, KeyboardEvent } from "react";
import { useId, useRef } from "react";

export type SegmentedOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

export type SegmentedControlProps = Omit<HTMLAttributes<HTMLDivElement>, "onChange"> & {
  ariaLabel?: string;
  options: readonly SegmentedOption[];
  value: string;
  onValueChange: (value: string) => void;
};

/** Управляемый одиночный выбор с клавиатурной навигацией по radio group. */
export function SegmentedControl({
  ariaLabel,
  options,
  value,
  onValueChange,
  className = "",
  ...props
}: SegmentedControlProps) {
  const inputRefs = useRef<Array<HTMLInputElement | null>>([]);
  const groupName = useId();
  const activeIndexes = options.flatMap((option, index) => (option.disabled ? [] : [index]));
  const selectedIndex = options.findIndex((option) => option.value === value && !option.disabled);
  const tabStopIndex = selectedIndex >= 0 ? selectedIndex : (activeIndexes[0] ?? -1);

  function moveTo(index: number) {
    const next = options[index];
    if (!next || next.disabled) return;
    onValueChange(next.value);
    inputRefs.current[index]?.focus();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>, currentIndex: number) {
    let targetIndex = -1;
    const currentPosition = activeIndexes.indexOf(currentIndex);
    if (currentPosition < 0 || activeIndexes.length === 0) return;

    if (event.key === "Home") targetIndex = activeIndexes[0] ?? -1;
    else if (event.key === "End") targetIndex = activeIndexes.at(-1) ?? -1;
    else if (["ArrowRight", "ArrowDown"].includes(event.key)) {
      targetIndex = activeIndexes[(currentPosition + 1) % activeIndexes.length] ?? -1;
    } else if (["ArrowLeft", "ArrowUp"].includes(event.key)) {
      const previous = (currentPosition - 1 + activeIndexes.length) % activeIndexes.length;
      targetIndex = activeIndexes[previous] ?? -1;
    }

    if (targetIndex < 0) return;
    event.preventDefault();
    moveTo(targetIndex);
  }

  return (
    <div
      {...props}
      className={`ui-segmented ${className}`.trim()}
      role="radiogroup"
      aria-label={ariaLabel ?? props["aria-label"]}
    >
      {options.map((option, index) => (
        <label
          key={option.value}
          className="ui-segmented__option"
          data-selected={option.value === value || undefined}
          data-disabled={option.disabled || undefined}
        >
          <input
            ref={(element) => {
              inputRefs.current[index] = element;
            }}
            className="ui-segmented__input"
            type="radio"
            name={groupName}
            value={option.value}
            checked={option.value === value}
            disabled={option.disabled}
            tabIndex={index === tabStopIndex ? 0 : -1}
            onChange={() => onValueChange(option.value)}
            onKeyDown={(event) => handleKeyDown(event, index)}
          />
          <span className="ui-segmented__label">{option.label}</span>
        </label>
      ))}
    </div>
  );
}
