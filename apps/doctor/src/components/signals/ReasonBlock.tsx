import type { Schemas } from "@compass/api-client";
import { ArrowRight } from "lucide-react";
import {
  changeText,
  lastTwo,
  type Mark,
  type Reason,
  reasonOf,
  scoreMarks,
} from "../../card/alerts";
import { formatDate, formatMomentInline } from "../../card/format";
import { useAssessmentHistory, useScaleDefinitions, useThresholds } from "../../card/queries";
import { signalView } from "../../card/signals";
import { LEVEL_ICON } from "../card/LevelBadge";

type Alert = Schemas["AlertOut"];
type Definition = Schemas["ScaleDefinitionOut"];
type Threshold = Schemas["ThresholdOut"];
type Assessment = Schemas["AssessmentOut"];

const MARK_TEXT = { mandatory: "Обязательно", desirable: "Желательно" } as const;
const MARK_TONE = { mandatory: "text-danger", desirable: "text-warning" } as const;

/**
 * Причина сигнала по правилу шкалы (кадры 03 и 03б): ответ на пункт со шкалой ответа
 * и порогом или сумма на линейке с порогами правил и «было → стало».
 */
export function ScaleReason({
  alert,
  reason,
  definition,
  thresholds,
  history,
}: {
  alert: Alert;
  reason: Reason;
  definition: Definition | undefined;
  thresholds: Threshold[];
  history: Assessment[];
}) {
  const firedAt = alert.triggers.at(-1)?.at;
  if (!reason.isScore) {
    const items = definition?.scale.items ?? [];
    const index = items.findIndex((i) => i.id === reason.key);
    const item = items[index];
    const limit = thresholds.find((t) => t.rule_id === alert.rule_id && t.item === reason.key);
    return (
      <div className="flex flex-col gap-3">
        <header className="flex justify-between gap-4">
          <h3 className="font-semibold">
            Пункт {index >= 0 ? index + 1 : reason.key} {definition?.scale.title ?? reason.scale}
          </h3>
          {firedAt && (
            <span className="text-sm text-ink-muted">Опрос {formatMomentInline(firedAt)}</span>
          )}
        </header>
        {item && <p className="text-sm text-ink-muted">«{item.text}»</p>}
        {item && <AnswerScale options={item.options} answer={reason.value} limit={limit?.value} />}
      </div>
    );
  }
  const max = Math.max(
    reason.value,
    ...(definition?.scale.scores.find((s) => s.id === reason.key)?.bands.map((b) => b.max) ?? [0]),
  );
  // «Было → стало» — на момент срабатывания: у старого сигнала не показываем поздние оценки.
  const points = lastTwo(history, reason.scale, reason.key, firedAt);
  const [before, now] = points.length === 2 ? points : [undefined, points[0]];
  const marks = scoreMarks(thresholds, reason.key);
  return (
    <div className="flex flex-col gap-4">
      <h3 className="font-semibold">
        {definition?.scale.title ?? reason.scale}, сумма баллов от 0 до {max}
      </h3>
      {before && now && (
        <p className="flex flex-wrap items-end gap-6">
          <Value value={before.value} band={before.band} muted />
          <ArrowRight aria-label="стало" className="mb-6 size-6 text-ink-muted" />
          <Value value={now.value} band={now.band} />
          <span className="flex flex-col">
            <span className="text-lg font-semibold">
              {changeText(before.value, now.value, before.at.slice(0, 10), now.at.slice(0, 10))}
            </span>
          </span>
        </p>
      )}
      <Ruler
        max={max}
        marks={marks}
        before={before}
        now={{ value: reason.value, at: now?.at ?? firedAt ?? "" }}
      />
    </div>
  );
}

/**
 * Линейка суммы 0…max (кадр 03б): сверху пороги правил, на линии — прошлый и текущий балл
 * со стрелкой между ними, под линией — баллы и даты. Отрезок от порога окрашен его уровнем.
 */
function Ruler({
  max,
  marks,
  before,
  now,
}: {
  max: number;
  marks: Mark[];
  before: { value: number; at: string } | undefined;
  now: { value: number; at: string };
}) {
  const at = (v: number) => `${(Math.min(v, max) / Math.max(max, 1)) * 100}%`;
  const label = `Балл ${now.value} из ${max}${before ? `, было ${before.value}` : ""}. ${marks
    .map((m) => `${MARK_TEXT[m.level]} от ${m.value}`)
    .join(". ")}`;
  const points = before ? [before, now] : [now];
  return (
    <div role="img" aria-label={label} className="mx-6 flex flex-col">
      <div className="relative h-6">
        {marks.map((m) => {
          const Icon = LEVEL_ICON[m.level];
          return (
            <span
              key={`${m.value}-${m.level}`}
              className={`absolute bottom-1 flex -translate-x-1/2 items-center gap-1 text-xs font-medium whitespace-nowrap ${MARK_TONE[m.level]}`}
              style={{ left: at(m.value) }}
            >
              <Icon aria-hidden className="size-3.5" />
              {MARK_TEXT[m.level]} от {m.value}
            </span>
          );
        })}
      </div>
      <div className="relative h-5">
        <span className="absolute top-1/2 -left-5 -translate-y-1/2 text-xs text-ink-muted">0</span>
        <span className="absolute top-1/2 -right-6 -translate-y-1/2 text-xs text-ink-muted">
          {max}
        </span>
        <span className="absolute top-1/2 right-0 left-0 h-0.5 -translate-y-1/2 bg-line" />
        {marks.map((m, i) => (
          <span key={`${m.value}-${m.level}`}>
            <span
              className={`absolute top-1/2 h-0.5 -translate-y-1/2 ${m.level === "mandatory" ? "bg-danger" : "bg-warning"}`}
              style={{ left: at(m.value), right: `calc(100% - ${at(marks[i + 1]?.value ?? max)})` }}
            />
            <span
              className={`absolute -top-1 h-7 border-l border-dashed ${m.level === "mandatory" ? "border-danger" : "border-warning"}`}
              style={{ left: at(m.value) }}
            />
          </span>
        ))}
        {before && (
          <span
            className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-primary"
            style={{
              left: at(Math.min(before.value, now.value)),
              right: `calc(100% - ${at(Math.max(before.value, now.value))})`,
            }}
          />
        )}
        {points.map((p, i) => (
          <span
            key={p.at || i}
            className={`absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-primary ${p === now ? "bg-primary" : "bg-surface"}`}
            style={{ left: at(p.value) }}
          />
        ))}
      </div>
      <div className="relative h-9">
        {points.map((p, i) => (
          <span
            key={p.at || i}
            className="absolute top-0.5 flex -translate-x-1/2 flex-col items-center text-xs"
            style={{ left: at(p.value) }}
          >
            <span className="font-semibold">{p.value}</span>
            {p.at && <span className="text-ink-muted">{formatDate(p.at)}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}

function Value({
  value,
  band,
  muted = false,
}: {
  value: number;
  band: string | null;
  muted?: boolean;
}) {
  return (
    <span className="flex flex-col">
      <span className={`text-3xl font-semibold ${muted ? "text-ink-muted" : ""}`}>{value}</span>
      <span className={`text-sm ${muted ? "text-ink-muted" : "font-semibold"}`}>{band ?? ""}</span>
    </span>
  );
}

/** Шкала ответа на пункт: варианты, порог правила и отмеченный ответ (кадр 03). */
function AnswerScale({
  options,
  answer,
  limit,
}: {
  options: Schemas["Option"][];
  answer: number;
  limit: number | undefined;
}) {
  const chosen = options.find((o) => o.value === answer);
  const first = options[0]?.value;
  const last = options.at(-1)?.value;
  return (
    <div>
      <p className="sr-only">
        Ответ: {answer}, {chosen?.text ?? ""}.{limit !== undefined && ` Порог: от ${limit} балла.`}
      </p>
      <ol
        aria-hidden
        className="grid"
        style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}
      >
        {options.map((o) => {
          const over = limit !== undefined && o.value >= limit;
          const picked = o.value === answer;
          return (
            <li key={o.value} className="relative flex flex-col items-center gap-1 text-center">
              {limit === o.value && (
                <span className="absolute top-0 left-0 -translate-x-1/2 text-xs font-medium text-danger">
                  Порог: от {limit}
                </span>
              )}
              {picked && <span className="text-xs font-semibold text-danger">Ответ</span>}
              {!picked && <span className="text-xs">&nbsp;</span>}
              <span className="relative flex h-5 w-full items-center justify-center">
                {o.value !== first && (
                  <span
                    className={`absolute top-1/2 left-0 h-0.5 w-1/2 -translate-y-1/2 ${over ? "bg-danger" : "bg-line"}`}
                  />
                )}
                {o.value !== last && (
                  <span
                    className={`absolute top-1/2 right-0 h-0.5 w-1/2 -translate-y-1/2 ${limit !== undefined && o.value + 1 >= limit ? "bg-danger" : "bg-line"}`}
                  />
                )}
                <span
                  className={`relative size-3.5 rounded-full border-2 ${over ? "border-danger" : "border-ink-muted"} ${picked ? "bg-danger" : "bg-surface"}`}
                />
              </span>
              <span className={`text-sm ${picked ? "font-semibold text-danger" : ""}`}>
                {o.value}
              </span>
              <span className={`text-xs ${picked ? "font-semibold text-ink" : "text-ink-muted"}`}>
                {o.text}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/**
 * Причина сигнала с данными: шкала по правилу или цитаты AI (спека #95, раздел 5).
 * Нужна экрану сигнала и листу направления (#35).
 */
export function SignalReason({ alert }: { alert: Alert }) {
  const reason = reasonOf(alert);
  const definitions = useScaleDefinitions(reason ? [reason.scale] : []);
  const thresholds = useThresholds(reason?.scale);
  const history = useAssessmentHistory(alert.patient_id);
  if (reason) {
    return (
      <ScaleReason
        alert={alert}
        reason={reason}
        definition={definitions.get(reason.scale)}
        thresholds={thresholds.data ?? []}
        history={history.data ?? []}
      />
    );
  }
  const view = signalView(alert);
  if (view.quotes.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-2">
      {view.quotes.map((q) => (
        <blockquote key={q.text} className="border-l-2 border-ink-muted pl-3 italic">
          «{q.text}»
          {!q.found && (
            <span className="ml-2 text-xs not-italic text-ink-muted">
              (цитата не найдена в тексте)
            </span>
          )}
        </blockquote>
      ))}
      {view.recordLabel && (
        <p className="text-xs text-ink-muted">{view.recordLabel}, по тексту, AI</p>
      )}
    </div>
  );
}
