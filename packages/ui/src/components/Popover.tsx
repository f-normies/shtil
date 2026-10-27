import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

type PopoverSide = "top" | "bottom";
type PopoverPosition = {
  top: number;
  left: number;
  side: PopoverSide;
  arrowLeft: number;
  contentMaxHeight: number;
};

export type PopoverProps = {
  trigger: ReactNode;
  label: string;
  children: ReactNode;
  placement?: PopoverSide;
  className?: string;
  triggerClassName?: string;
  panelClassName?: string;
};

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';
const EDGE = 8;
const GAP = 10;

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

/** Неблокирующий поповер с клавиатурной активацией и возвратом фокуса при Esc. */
export function Popover({
  trigger,
  label,
  children,
  placement = "bottom",
  className = "",
  triggerClassName = "",
  panelClassName = "",
}: PopoverProps) {
  const id = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const focusContentOnOpen = useRef(false);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PopoverPosition | null>(null);

  const measurePosition = useCallback((): PopoverPosition | null => {
    const triggerElement = triggerRef.current;
    const panel = panelRef.current;
    if (!triggerElement || !panel) return null;
    const content = panel.querySelector<HTMLElement>(".ui-popover__content");
    if (!content) return null;

    const anchor = triggerElement.getBoundingClientRect();
    const popover = panel.getBoundingClientRect();
    const panelStyles = window.getComputedStyle(panel);
    const panelChromeHeight =
      Number.parseFloat(panelStyles.paddingTop) +
      Number.parseFloat(panelStyles.paddingBottom) +
      Number.parseFloat(panelStyles.borderTopWidth) +
      Number.parseFloat(panelStyles.borderBottomWidth);
    const naturalHeight = content.scrollHeight + panelChromeHeight;
    const spaceAbove = anchor.top - EDGE - GAP;
    const spaceBelow = window.innerHeight - anchor.bottom - EDGE - GAP;
    let side = placement;
    if (side === "bottom" && naturalHeight > spaceBelow && spaceAbove > spaceBelow) side = "top";
    if (side === "top" && naturalHeight > spaceAbove && spaceBelow > spaceAbove) side = "bottom";

    const availableHeight = side === "bottom" ? spaceBelow : spaceAbove;
    const contentMaxHeight = Math.max(
      0,
      Math.min(content.scrollHeight, availableHeight - panelChromeHeight),
    );
    const panelHeight = Math.min(naturalHeight, availableHeight);

    const idealLeft = anchor.left + anchor.width / 2 - popover.width / 2;
    const left = clamp(idealLeft, EDGE, window.innerWidth - popover.width - EDGE);
    const top = side === "bottom" ? anchor.bottom + GAP : anchor.top - panelHeight - GAP;
    const arrowLeft = clamp(anchor.left + anchor.width / 2 - left, 14, popover.width - 14);

    return { top, left, side, arrowLeft, contentMaxHeight };
  }, [placement]);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    setPosition(measurePosition());
  }, [open, measurePosition]);

  useLayoutEffect(() => {
    if (!open || !position || !focusContentOnOpen.current) return;
    const panel = panelRef.current;
    const target = panel?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR) ?? panel;
    target?.focus({ preventScroll: true });
    focusContentOnOpen.current = false;
  }, [open, position]);

  useEffect(() => {
    if (!open) return;

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      focusContentOnOpen.current = false;
      triggerRef.current?.focus();
    }

    function handlePointerDown(event: globalThis.PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      focusContentOnOpen.current = false;
      setOpen(false);
    }

    function updatePosition() {
      const next = measurePosition();
      if (next) setPosition(next);
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("resize", updatePosition);
    document.addEventListener("scroll", updatePosition, true);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("resize", updatePosition);
      document.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, measurePosition]);

  const panelStyle: CSSProperties | undefined = position
    ? ({
        left: `${position.left}px`,
        top: `${position.top}px`,
        "--ui-popover-arrow-left": `${position.arrowLeft}px`,
      } as CSSProperties)
    : undefined;
  const side = position?.side ?? placement;

  return (
    <span className={`ui-popover ${className}`.trim()}>
      <button
        ref={triggerRef}
        type="button"
        className={`ui-popover__trigger ${triggerClassName}`.trim()}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => {
          focusContentOnOpen.current = !open;
          setOpen((wasOpen) => !wasOpen);
        }}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={panelRef}
          id={id}
          className={`ui-popover__panel ${panelClassName}`.trim()}
          role="dialog"
          aria-label={label}
          tabIndex={-1}
          data-side={side}
          data-positioned={Boolean(position)}
          style={panelStyle}
        >
          <div
            className="ui-popover__content"
            style={position ? { maxHeight: `${position.contentMaxHeight}px` } : undefined}
          >
            {children}
          </div>
          <svg
            className="ui-popover__arrow"
            width="16"
            height="8"
            viewBox="0 0 16 8"
            preserveAspectRatio="none"
            aria-hidden="true"
            focusable="false"
          >
            <path
              d={
                side === "bottom"
                  ? "M0 8C4 8 6 0 8 0C10 0 12 8 16 8H0Z"
                  : "M0 0C4 0 6 8 8 8C10 8 12 0 16 0H0Z"
              }
              fill="var(--color-surface)"
            />
          </svg>
        </div>
      )}
    </span>
  );
}
