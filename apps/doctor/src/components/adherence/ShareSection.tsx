import type { Schemas } from "@compass/api-client";
import { type ReactNode, useEffect, useState } from "react";
import {
  courseNote,
  dosesText,
  missedText,
  percentOf,
  periodTitle,
  regimenLine,
  type Sex,
  shareNote,
} from "../../card/adherence";
import { Section } from "../signals/Section";

type Adherence = Schemas["AdherenceOut"];

/**
 * Доля приёма (кадр 07): процент со знаменателем словами, период и источник рядом,
 * полоса доли, «Пропущено», «Нет отметки» и схема. Без отметок доля не показывается.
 */
export function ShareSection({ adherence, sex }: { adherence: Adherence; sex: Sex }) {
  const percent = percentOf(adherence);
  const missed = missedText(adherence);
  const note = courseNote(adherence);
  if (!adherence.period) {
    return null;
  }
  return (
    <Section title={periodTitle(adherence.period)} flush>
      {percent === null ? (
        <p className="px-4 py-3 text-[15px] text-ink">
          {sex === "female" ? "Пациентка ещё не отмечала приём" : "Пациент ещё не отмечал приём"}
        </p>
      ) : (
        <div className="flex flex-col gap-3 px-4 pt-4 pb-4">
          <p className="flex items-center gap-3">
            <span className="text-[40px] leading-[44px] font-semibold tracking-tight text-ink">
              {percent}%
            </span>
            <span className="flex flex-col">
              <span className="text-[15px] leading-5 font-semibold text-ink">
                {dosesText(adherence.taken, adherence.planned)}
              </span>
              <span className="text-[13px] leading-[18px] text-ink-muted">{shareNote(sex)}</span>
            </span>
          </p>
          <ShareBar percent={percent} />
        </div>
      )}
      <dl className="flex flex-col text-[15px] [&>div]:border-t [&>div]:border-separator">
        {missed && <Row name="Пропущено" value={missed} />}
        {adherence.unmarked > 0 && percent !== null && (
          <Row name="Нет отметки" value={`${adherence.unmarked}`} />
        )}
        <Row
          name="Схема"
          value={
            <span className="flex flex-col">
              {adherence.prescriptions.map((p) => (
                <span key={p.id}>{regimenLine(p)}</span>
              ))}
              {note && <span className="text-[13px] text-ink-muted">{note}</span>}
            </span>
          }
        />
      </dl>
    </Section>
  );
}

function Row({ name, value }: { name: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[156px_1fr] gap-x-4 px-4 py-3">
      <dt className="text-ink-muted">{name}</dt>
      <dd className="text-ink">{value}</dd>
    </div>
  );
}

/** Полоса доли: заполняется слева направо за 200 мс; «уменьшить движение» — сразу. */
function ShareBar({ percent }: { percent: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(percent));
    return () => cancelAnimationFrame(frame);
  }, [percent]);
  return (
    <div aria-hidden className="h-2 overflow-hidden rounded-full bg-surface-sunken">
      <div
        className="h-full rounded-full bg-primary transition-[width] duration-200 ease-out motion-reduce:transition-none"
        style={{ width: `${shown}%` }}
      />
    </div>
  );
}
