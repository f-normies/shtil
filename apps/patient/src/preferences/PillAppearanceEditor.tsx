import type { Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useState } from "react";
import {
  COLOR_LABELS,
  COLOR_TINTS,
  describeAppearance,
  type PillColor,
  type PillDraft,
  type PillShape,
  pillDraftChanged,
  pillDraftFor,
  pillDraftReady,
  SHAPE_LABELS,
} from "./pill-appearance";
import { type PillSaveState, usePillAppearanceSaver } from "./pill-appearance-saver";

type Item = Schemas["ScheduleItem"];

type EditorProps = {
  items: Item[];
  drafts: Record<string, PillDraft>;
  onChange: (prescriptionId: string, draft: PillDraft) => void;
  states: Record<string, PillSaveState>;
  disabled?: boolean;
  onReset?: (item: Item) => void;
};

/** Плитки формы и кружки цвета по каждому назначению (кадр «Пациент · 09»). Выбор хранит родитель. */
export function PillAppearanceEditor({
  items,
  drafts,
  onChange,
  states,
  disabled = false,
  onReset,
}: EditorProps) {
  if (items.length === 0) {
    return <p className="text-sm text-ink-muted">Действующих назначений нет.</p>;
  }
  return (
    <div className="flex flex-col gap-6">
      {items.map((item) => (
        <AppearanceCard
          key={item.prescription_id}
          item={item}
          draft={drafts[item.prescription_id] ?? pillDraftFor(item)}
          state={states[item.prescription_id] ?? { status: "idle" }}
          disabled={disabled}
          onChange={(draft) => onChange(item.prescription_id, draft)}
          onReset={onReset ? () => onReset(item) : undefined}
        />
      ))}
    </div>
  );
}

function AppearanceCard({
  item,
  draft,
  state,
  disabled,
  onChange,
  onReset,
}: {
  item: Item;
  draft: PillDraft;
  state: PillSaveState;
  disabled: boolean;
  onChange: (draft: PillDraft) => void;
  onReset?: () => void;
}) {
  const origin =
    item.appearance_origin === "patient"
      ? "Ваш выбор"
      : item.appearance_origin === "instruction"
        ? "Как в инструкции"
        : "Вид не указан";
  return (
    <article aria-label={item.trade_name} className="flex flex-col gap-5">
      <div className="flex items-center gap-4 rounded-3xl bg-surface p-4 shadow-sm">
        <PillGlyph shape={draft.shape} color={draft.color} size={56} />
        <div className="min-w-0 flex-1">
          <h3 className="text-[22px] font-semibold leading-7 tracking-tight text-ink">
            {item.trade_name}
          </h3>
          <p className="text-[15px] leading-5 text-ink-muted">Таблетки {item.dose_mg} мг</p>
          <span className="mt-2 inline-block rounded-full bg-accent-calm-soft px-2.5 py-1 text-[13px] font-medium leading-[18px] text-primary-text">
            {origin}
          </span>
        </div>
      </div>
      <fieldset className="flex flex-col gap-2" disabled={disabled}>
        <legend className="mb-2 text-[13px] font-medium leading-[18px] text-ink-muted">
          Форма
        </legend>
        <div className="grid grid-cols-3 gap-3">
          {(Object.keys(SHAPE_LABELS) as PillShape[]).map((shape) => {
            const selected = draft.shape === shape;
            return (
              <button
                key={shape}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange({ ...draft, shape })}
                className={`flex min-h-[108px] flex-col items-center justify-center gap-2 rounded-2xl border bg-surface py-3 text-[15px] leading-5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${selected ? "border-2 border-primary bg-accent-calm-soft text-primary-text" : "border-line text-ink"}`}
              >
                <PillGlyph shape={shape} color={draft.color ?? "white"} size={48} />
                {SHAPE_LABELS[shape]}
              </button>
            );
          })}
        </div>
      </fieldset>
      <fieldset className="flex flex-col gap-2" disabled={disabled}>
        <legend className="mb-2 text-[13px] font-medium leading-[18px] text-ink-muted">Цвет</legend>
        <div className="grid grid-cols-4 gap-2">
          {(Object.keys(COLOR_LABELS) as PillColor[]).map((color) => {
            const selected = draft.color === color;
            return (
              <button
                key={color}
                type="button"
                aria-pressed={selected}
                onClick={() => onChange({ ...draft, color })}
                className={`flex min-h-[76px] flex-col items-center gap-2 rounded-2xl px-0.5 py-2.5 text-[13px] font-medium leading-[18px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${selected ? "text-primary-text" : "text-ink"}`}
              >
                <span
                  aria-hidden="true"
                  className={`grid size-10 place-items-center rounded-full ${selected ? "border-2 border-primary" : "border border-transparent"}`}
                >
                  <span
                    className={`block size-7 rounded-full ${color === "white" ? "border-2 border-line-strong" : ""}`}
                    style={{ backgroundColor: COLOR_TINTS[color] }}
                  />
                </span>
                {COLOR_LABELS[color]}
              </button>
            );
          })}
        </div>
      </fieldset>
      {item.default_appearance && (
        <details className="rounded-2xl bg-surface px-4 py-3 text-sm">
          <summary className="cursor-pointer font-medium text-primary-text">Из инструкции</summary>
          <p className="mt-2 text-ink-muted">{item.default_appearance.description}</p>
          <a
            className="mt-1 inline-block font-medium text-primary underline"
            href={item.default_appearance.source.url}
            target="_blank"
            rel="noreferrer"
          >
            {item.default_appearance.source.title} · {item.default_appearance.source.locator}
          </a>
        </details>
      )}
      {state.status === "error" && (
        <Alert tone="error">
          {state.conflict
            ? "Внешний вид уже изменился на другом устройстве. Обновите экран и попробуйте снова."
            : "Не удалось сохранить вид таблетки. Проверьте связь и повторите."}
        </Alert>
      )}
      {state.status === "saved" && (
        <p role="status" className="text-sm font-medium text-primary-text">
          Сохранено: {describeAppearance(item.appearance)}.
        </p>
      )}
      {item.appearance_origin === "patient" && onReset && (
        <Button variant="secondary" onClick={onReset} disabled={disabled}>
          Вернуть вид по инструкции
        </Button>
      )}
    </article>
  );
}

/** Пиктограмма таблетки в мягком круге; белая таблетка получает контрастную обводку. */
export function PillGlyph({
  shape,
  color,
  size,
}: {
  shape: PillShape | null;
  color: PillColor | null;
  size: number;
}) {
  const tint = COLOR_TINTS[color ?? "white"];
  const stroke = color === "white" || color === null ? "var(--palette-brand-500)" : tint;
  return (
    <span
      role="img"
      aria-label={describeAppearance(shape && color ? { shape, color } : null)}
      className="grid shrink-0 place-items-center rounded-full bg-accent-calm-soft"
      style={{ width: size, height: size }}
    >
      <svg viewBox="0 0 40 40" width={size * 0.6} height={size * 0.6} aria-hidden="true">
        <title>{describeAppearance(shape && color ? { shape, color } : null)}</title>
        {shape === "capsule" ? (
          <>
            <rect
              x="4"
              y="13"
              width="32"
              height="14"
              rx="7"
              fill={tint}
              stroke={stroke}
              strokeWidth="2"
            />
            <line x1="20" y1="13" x2="20" y2="27" stroke={stroke} strokeWidth="2" />
          </>
        ) : shape === "oval" ? (
          <ellipse cx="20" cy="20" rx="16" ry="10" fill={tint} stroke={stroke} strokeWidth="2" />
        ) : (
          <>
            <circle cx="20" cy="20" r="15" fill={tint} stroke={stroke} strokeWidth="2" />
            <line x1="12" y1="20" x2="28" y2="20" stroke={stroke} strokeWidth="2" />
          </>
        )}
      </svg>
    </span>
  );
}

/** Раздел профиля «Вид таблеток»: редактор, «Сохранить вид» для изменённых назначений. */
export function PillAppearanceSection({
  items,
  ownerId,
  online,
}: {
  items: Item[];
  ownerId: string;
  online: boolean;
}) {
  const [drafts, setDrafts] = useState<Record<string, PillDraft>>({});
  const saver = usePillAppearanceSaver(ownerId);
  const changed = items.some((item) => {
    const draft = drafts[item.prescription_id];
    return draft !== undefined && pillDraftReady(draft) && pillDraftChanged(item, draft);
  });
  return (
    <div className="flex flex-col gap-4">
      <PillAppearanceEditor
        items={items}
        drafts={drafts}
        states={saver.states}
        disabled={!online || saver.pending}
        onChange={(id, draft) => setDrafts((current) => ({ ...current, [id]: draft }))}
        onReset={(item) => {
          setDrafts((current) => ({ ...current, [item.prescription_id]: pillDraftFor(item) }));
          void saver.save(item, null);
        }}
      />
      {items.length > 0 && (
        <Button
          variant="secondary"
          onClick={() => void saver.saveAll(items, drafts)}
          disabled={!online || !changed || saver.pending}
        >
          {saver.pending ? "Сохраняем…" : "Сохранить вид"}
        </Button>
      )}
      {items.some((item) => item.appearance_origin === "patient") && (
        <p className="text-xs text-ink-muted">
          Этот выбор меняет только отображение у вас. Назначение и приём препарата не меняются.
        </p>
      )}
      {!online && (
        <p className="text-sm text-ink-muted">Для сохранения настроек нужно подключение.</p>
      )}
    </div>
  );
}
