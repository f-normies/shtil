import { BrandMark } from "@compass/ui";
import {
  displayNameSuggestions,
  type EditablePreferences,
  MOOD_PALETTES,
  moodLevelColors,
  previewGreeting,
} from "./onboarding-flow";
import { TimePicker } from "./TimePicker";

type Field = keyof EditablePreferences;
type Props = {
  field: Field;
  values: EditablePreferences;
  onChange: (field: Field, value: EditablePreferences[Field]) => void;
  reminderMode?: "onboarding" | "profile";
  /** ФИО пациента: даёт чипы-подсказки и предпросмотр приветствия на шаге имени. */
  fullName?: string;
  disabled?: boolean;
};

const stories = [
  { value: "voice", title: "Голосом", note: "Можно рассказать, как прошёл день" },
  { value: "text", title: "Текстом", note: "Удобно написать коротко" },
] as const;

const textSizes = [
  { value: "normal", title: "Обычный", note: "Стандартный размер текста" },
  { value: "large", title: "Крупный", note: "Чуть крупнее" },
  { value: "extra_large", title: "Очень крупный", note: "Самый крупный размер" },
] as const;

export function PreferenceField({
  field,
  values,
  onChange,
  reminderMode,
  fullName,
  disabled = false,
}: Props) {
  if (field === "display_name") {
    return (
      <DisplayNameField
        value={values.display_name}
        fullName={fullName}
        disabled={disabled}
        onChange={(value) => onChange("display_name", value)}
      />
    );
  }

  if (field === "reminder_time") {
    return (
      <TimePicker
        value={values.reminder_time}
        onChange={(value) => onChange("reminder_time", value)}
        mode={reminderMode ?? "onboarding"}
        disabled={disabled}
      />
    );
  }

  if (field === "mood_palette") {
    return (
      <MoodPaletteField
        value={values.mood_palette}
        disabled={disabled}
        onChange={(value) => onChange("mood_palette", value)}
      />
    );
  }

  if (field === "story_mode") {
    return (
      <ChoiceGroup
        label="Способ рассказать о дне"
        value={values.story_mode}
        choices={stories}
        onChange={(value) => onChange("story_mode", value)}
        disabled={disabled}
      />
    );
  }

  return (
    <ChoiceGroup
      label="Размер текста"
      value={values.text_size}
      choices={textSizes}
      onChange={(value) => onChange("text_size", value)}
      disabled={disabled}
    />
  );
}

function DisplayNameField({
  value,
  fullName,
  disabled,
  onChange,
}: {
  value: string;
  fullName?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const suggestions = fullName ? displayNameSuggestions(fullName) : [];
  return (
    <div className="flex flex-col gap-3">
      <label className="flex flex-col gap-2 text-sm font-medium" htmlFor="display-name">
        Имя
        <input
          id="display-name"
          name="display_name"
          autoComplete="nickname"
          maxLength={100}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          className="min-h-12 rounded-xl border border-line bg-canvas px-4 text-base text-ink outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20"
        />
      </label>
      {suggestions.length > 0 && (
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Подсказки имени</legend>
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              aria-pressed={value === suggestion}
              disabled={disabled}
              onClick={() => onChange(suggestion)}
              className={`min-h-11 rounded-full border px-4 text-[15px] font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${value === suggestion ? "border-primary bg-primary-soft text-primary-text" : "border-line bg-surface text-ink"}`}
            >
              {suggestion}
            </button>
          ))}
        </fieldset>
      )}
      {fullName !== undefined && (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] font-medium leading-[18px] text-ink-muted">
            Так будет на главном экране
          </p>
          <div className="rounded-3xl bg-surface p-5 shadow-sm">
            <BrandMark persona="patient" decorative className="h-8 w-8" />
            <p className="mt-6 text-[30px] font-semibold leading-9 tracking-tight text-ink">
              {previewGreeting(value, new Date().getHours())}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function MoodDots({ colors, size }: { colors: readonly string[]; size: number }) {
  return (
    <span aria-hidden="true" className="flex items-center gap-2">
      {moodLevelColors(colors).map(({ level, color }) => (
        <span
          key={level}
          className="block rounded-full"
          style={{ width: size, height: size, backgroundColor: color }}
        />
      ))}
    </span>
  );
}

function MoodPaletteField({
  value,
  disabled,
  onChange,
}: {
  value: EditablePreferences["mood_palette"];
  disabled: boolean;
  onChange: (value: EditablePreferences["mood_palette"]) => void;
}) {
  const current = MOOD_PALETTES.find((palette) => palette.value === value) ?? MOOD_PALETTES[0];
  return (
    <div className="flex flex-col gap-5">
      <div className="rounded-3xl bg-surface p-5 shadow-sm">
        <p className="text-[13px] font-medium leading-[18px] text-ink-muted">
          Так будет выглядеть шкала
        </p>
        <div className="mt-4 flex justify-between">
          <MoodDots colors={current.colors} size={48} />
        </div>
        <div className="mt-3 flex justify-between text-[15px] leading-5 text-ink">
          <span>Очень тяжело</span>
          <span>Хорошо</span>
        </div>
      </div>
      <fieldset className="flex flex-col gap-3">
        <legend className="sr-only">Набор цветов</legend>
        {MOOD_PALETTES.map((palette) => {
          const selected = value === palette.value;
          return (
            <button
              key={palette.value}
              type="button"
              aria-pressed={selected}
              disabled={disabled}
              onClick={() => onChange(palette.value)}
              className={`flex min-h-[76px] items-center gap-3 rounded-2xl border bg-surface px-4 py-4 text-left transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${selected ? "border-2 border-primary bg-primary-soft" : "border-line"}`}
            >
              <span
                aria-hidden="true"
                className={`grid size-6 shrink-0 place-items-center rounded-full border text-sm ${selected ? "border-primary bg-primary text-primary-ink" : "border-line-strong"}`}
              >
                {selected ? "✓" : ""}
              </span>
              <span className="flex min-w-0 flex-col gap-1.5">
                <span className="text-[17px] font-semibold leading-6 text-ink">
                  {palette.title}
                </span>
                <MoodDots colors={palette.colors} size={16} />
              </span>
            </button>
          );
        })}
      </fieldset>
    </div>
  );
}

function ChoiceGroup<T extends string>({
  label,
  value,
  choices,
  onChange,
  disabled,
}: {
  label: string;
  value: T;
  choices: readonly { value: T; title: string; note: string }[];
  onChange: (value: T) => void;
  disabled: boolean;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium text-ink">{label}</legend>
      {choices.map((choice) => {
        const selected = value === choice.value;
        return (
          <button
            key={choice.value}
            type="button"
            aria-pressed={selected}
            disabled={disabled}
            onClick={() => onChange(choice.value)}
            className={`flex min-h-16 items-center gap-3 rounded-xl border px-4 py-3 text-left transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${selected ? "border-primary bg-accent-calm-soft" : "border-line bg-surface"}`}
          >
            <span
              aria-hidden="true"
              className="grid size-6 shrink-0 place-items-center rounded-full border border-current text-sm"
            >
              {selected ? "✓" : ""}
            </span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="font-medium text-ink">{choice.title}</span>
              <span className="text-sm font-normal text-ink-muted">{choice.note}</span>
            </span>
          </button>
        );
      })}
    </fieldset>
  );
}
