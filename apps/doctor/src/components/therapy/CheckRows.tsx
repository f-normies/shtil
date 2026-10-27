import type { Schemas } from "@compass/api-client";
import { StatusIcon } from "@compass/ui";
import { BookOpen, Check, CircleHelp, TriangleAlert, X } from "lucide-react";
import { type KeyboardEvent, useId, useState } from "react";
import { checksSummary } from "../../card/regimen";
import { checkBasis, refsText } from "../../card/sources";
import { checkLevel, checkWord, safetySummary } from "../../card/therapy";
import { LEVEL_ICON } from "../card/LevelBadge";
import { Section } from "../signals/Section";
import { SourceButton } from "../source/SourceButton";

type CheckOut = Schemas["CheckOut"];

/** Статус — значком и словом, цвет только дополняет (спека #28, 8.3). */
const STATUS = {
  pass: { Icon: Check, tone: "text-success" },
  fail: { Icon: X, tone: "text-danger" },
  warn: { Icon: TriangleAlert, tone: "text-advised" },
  manual: { Icon: CircleHelp, tone: "text-ink" },
} as const;

/** Уровень словом — значок уровня, как у сигнала (спека #36). */
const LEVEL_TONE = { mandatory: "text-required", desirable: "text-advised" } as const;

function statusView(check: CheckOut) {
  const level = checkLevel(check);
  return level ? { Icon: LEVEL_ICON[level], tone: LEVEL_TONE[level] } : STATUS[check.status];
}

/**
 * «Проверки по инструкции» (кадр «Врач v4 · 10»): итог у заголовка, строка — значок,
 * название, подробность и статус словом. Список с выбором: фокус на списке, ↑↓, Home и
 * End выбирают строку, клик тоже. Источник выбранной проверки открывается в инспекторе.
 * Найденные противопоказания (#29) выделены.
 */
export function CheckRows({ checks }: { checks: readonly CheckOut[] }) {
  const base = useId();
  const [selected, setSelected] = useState<number | null>(null);
  const safety = safetySummary(checks);
  const blocked = checks.some((c) => c.id.startsWith("contra:") && c.status === "fail");
  const chosen = selected !== null ? checks[selected] : undefined;
  const basis = chosen ? checkBasis(chosen) : null;

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const last = checks.length - 1;
    const at = selected ?? -1;
    const next =
      e.key === "ArrowDown"
        ? Math.min(at + 1, last)
        : e.key === "ArrowUp"
          ? Math.max(at - 1, 0)
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? last
              : null;
    if (next !== null && last >= 0) {
      e.preventDefault();
      setSelected(next);
    }
  };

  return (
    <Section title="Проверки по инструкции" aside={checksSummary(checks)} flush>
      {safety && (
        <p
          className={`mx-4 mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold ${blocked ? "bg-danger-soft text-danger" : "bg-surface-sunken text-ink"}`}
        >
          {blocked && (
            <>
              <StatusIcon status="required" decorative />
              <span>Обязательно.</span>
            </>
          )}
          {safety}
        </p>
      )}
      <div
        role="listbox"
        tabIndex={0}
        aria-label="Проверки по инструкции"
        aria-activedescendant={selected !== null ? `${base}-${selected}` : undefined}
        onKeyDown={onKeyDown}
        onFocus={() => setSelected((s) => s ?? (checks.length > 0 ? 0 : null))}
        className="flex flex-col rounded-xl focus-visible:outline-2 focus-visible:outline-focus [&>div+div]:border-t [&>div+div]:border-separator"
      >
        {checks.map((c, i) => {
          const { Icon, tone } = statusView(c);
          const active = i === selected;
          return (
            // biome-ignore lint/a11y/useFocusableInteractive: фокус у списка (aria-activedescendant).
            // biome-ignore lint/a11y/useKeyWithClickEvents: клавиатура — у списка.
            <div
              key={c.id}
              id={`${base}-${i}`}
              role="option"
              aria-selected={active}
              onClick={() => setSelected(i)}
              className={`flex cursor-default gap-3 rounded-xl px-4 py-3 ${active ? "bg-primary text-primary-ink" : "hover:bg-surface-sunken"}`}
            >
              <Icon aria-hidden className={`mt-0.5 size-5 shrink-0 ${active ? "" : tone}`} />
              <div className="flex min-w-0 flex-1 flex-col">
                <p className="flex justify-between gap-3">
                  <span className="text-[15px]">{c.title}</span>
                  <span className={`shrink-0 text-sm font-semibold ${active ? "" : tone}`}>
                    {capitalize(checkWord(c))}
                  </span>
                </p>
                <p className={`text-[13px] ${active ? "" : "text-ink-muted"}`}>{c.detail}</p>
                {c.status !== "pass" && (c.actions ?? []).length > 0 && (
                  <p className="text-[13px]">Что делать: {(c.actions ?? []).join("; ")}</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {basis && (
        <div className="mx-4 mb-3 flex w-fit items-center gap-2 border-t border-separator pt-3 text-[13px] font-semibold text-primary-text underline-offset-2 hover:underline">
          <BookOpen aria-hidden className="size-4" />
          <SourceButton basis={basis}>{refsText(basis.refs)}</SourceButton>
        </div>
      )}
    </Section>
  );
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
