import type { Schemas } from "@compass/api-client";

export type OnboardingStepId =
  | "welcome"
  | "name"
  | "reminder"
  | "mood"
  | "story"
  | "pills"
  | "text"
  | "done";

const STEPS_WITH_PILLS: OnboardingStepId[] = [
  "welcome",
  "name",
  "reminder",
  "mood",
  "story",
  "pills",
  "text",
  "done",
];

export type EditablePreferences = Pick<
  Schemas["PatientPreferencesOut"],
  "display_name" | "reminder_time" | "mood_palette" | "story_mode" | "text_size"
>;

type SkipInput = {
  currentStep: OnboardingStepId;
  steps: OnboardingStepId[];
  initialValues: EditablePreferences;
  draftValues: EditablePreferences;
  onboardingCompleted: boolean;
};

type SkipResult =
  | { kind: "complete"; values: EditablePreferences }
  | { kind: "advance"; step: OnboardingStepId; values: EditablePreferences };

export function skipOnboardingStep(input: SkipInput): SkipResult {
  if (input.currentStep === "welcome" && !input.onboardingCompleted) {
    return { kind: "complete", values: input.initialValues };
  }

  const currentIndex = input.steps.indexOf(input.currentStep);
  if (currentIndex < 0) throw new Error(`Unknown onboarding step: ${input.currentStep}`);
  const nextStep = input.steps[currentIndex + 1];
  if (!nextStep) return { kind: "complete", values: input.draftValues };

  const fieldByStep = {
    name: "display_name",
    reminder: "reminder_time",
    mood: "mood_palette",
    story: "story_mode",
    text: "text_size",
  } as const;
  const field = fieldByStep[input.currentStep as keyof typeof fieldByStep];
  const values = field
    ? { ...input.draftValues, [field]: input.initialValues[field] }
    : input.draftValues;

  return { kind: "advance", step: nextStep, values };
}

export function getOnboardingSteps(hasActivePrescription: boolean): OnboardingStepId[] {
  return hasActivePrescription
    ? [...STEPS_WITH_PILLS]
    : STEPS_WITH_PILLS.filter((step) => step !== "pills");
}

export function changedPreferences(
  initial: EditablePreferences,
  values: EditablePreferences,
): Partial<EditablePreferences> {
  return Object.fromEntries(
    (Object.keys(initial) as (keyof EditablePreferences)[])
      .filter((key) => initial[key] !== values[key])
      .map((key) => [key, values[key]]),
  ) as Partial<EditablePreferences>;
}

export function moodAttribute(mood: EditablePreferences["mood_palette"]): string {
  return { warm: "teplyy", green: "zelenyy", calm: "spokoynyy" }[mood];
}

/** Луна под колесом времени с 00:00 до 04:59 — в онбординге (06б) и в профиле (28). */
export function showNightMoon(time: string): boolean {
  const hour = Number(time.slice(0, 2));
  return time.length >= 2 && Number.isInteger(hour) && hour >= 0 && hour <= 4;
}

/** Шаг онбординга в адресе: `?step=mood`. Неизвестное значение отбрасывается (P1-13). */
export function parseOnboardingSearch(search: Record<string, unknown>): {
  step?: OnboardingStepId;
} {
  const step = search.step;
  return typeof step === "string" && STEPS_WITH_PILLS.includes(step as OnboardingStepId)
    ? { step: step as OnboardingStepId }
    : {};
}

/** Шаг из адреса, если он есть в текущем наборе шагов; иначе приветствие. */
export function resolveOnboardingStep(
  requested: OnboardingStepId | undefined,
  steps: OnboardingStepId[],
): OnboardingStepId {
  return requested && steps.includes(requested) ? requested : "welcome";
}

/** Отрезок прогресса: пройденные — основной цвет, остальные — небесный, видимый на сцене. */
export function segmentClassName(index: number, stepNumber: number): string {
  return `h-1 min-w-0 flex-1 rounded-full ${index < stepNumber ? "bg-primary" : "bg-accent-sky"}`;
}

function nameParts(fullName: string): string[] {
  return fullName.trim().split(/\s+/).filter(Boolean);
}

/** Имя и отчество из ФИО: при трёх словах фамилия идёт первой, при двух — имя. */
function firstNameAndPatronymic(fullName: string): [string | undefined, string | undefined] {
  const parts = nameParts(fullName);
  return parts.length >= 3 ? [parts[1], parts[2]] : [parts[0], parts[1]];
}

/** Имя по умолчанию («Лебедева Марина Викторовна» → «Марина»). */
export function defaultDisplayName(fullName: string): string {
  return firstNameAndPatronymic(fullName)[0] ?? "";
}

/** Подсказки-чипы: имя и имя с отчеством, если отчество есть в ФИО. */
export function displayNameSuggestions(fullName: string): string[] {
  const [firstName, patronymic] = firstNameAndPatronymic(fullName);
  if (!firstName) return [];
  return patronymic ? [firstName, `${firstName} ${patronymic}`] : [firstName];
}

/** Приветствие главного экрана для предпросмотра имени; часы — местное время. */
export function previewGreeting(name: string, hour: number): string {
  const period =
    hour >= 23 || hour < 5
      ? "Добрый вечер"
      : hour < 11
        ? "Доброе утро"
        : hour < 17
          ? "Добрый день"
          : "Добрый вечер";
  const trimmed = name.trim();
  return trimmed ? `${period}, ${trimmed}` : period;
}

/** Когда будет первая отметка: сегодня, если время напоминания ещё не наступило. */
export function firstCheckInText(reminderTime: string, now: string): string {
  const day = reminderTime > now ? "сегодня" : "завтра";
  return `Первая отметка ${day} в ${reminderTime}.`;
}

export const MOOD_PALETTES = [
  {
    value: "warm",
    title: "Тёплый — хорошо",
    colors: [
      "var(--palette-brand-300)",
      "var(--palette-brand-200)",
      "var(--palette-mint-300)",
      "var(--palette-butter-200)",
      "var(--palette-peach-300)",
    ],
  },
  {
    value: "green",
    title: "Зелёный — хорошо",
    colors: [
      "var(--palette-brand-300)",
      "var(--palette-brand-200)",
      "var(--palette-mint-200)",
      "var(--palette-mint-300)",
      "var(--palette-mint-400)",
    ],
  },
  {
    value: "calm",
    title: "Спокойный, без оценки",
    colors: [
      "var(--palette-brand-200)",
      "var(--palette-brand-300)",
      "var(--palette-brand-400)",
      "var(--palette-brand-500)",
      "var(--palette-brand-600)",
    ],
  },
] as const satisfies readonly {
  value: EditablePreferences["mood_palette"];
  title: string;
  colors: readonly string[];
}[];

/** Название набора цветов, как в макете 07. Цвета наборов повторяют `styles.css`. */
export function moodPaletteLabel(palette: EditablePreferences["mood_palette"]): string {
  return MOOD_PALETTES.find((item) => item.value === palette)?.title ?? palette;
}

const MOOD_LEVELS = [1, 2, 3, 4, 5] as const;

/** Кружки шкалы с устойчивым ключом — уровнем от «Очень тяжело» (1) до «Хорошо» (5). */
export function moodLevelColors(
  colors: readonly string[],
): { level: (typeof MOOD_LEVELS)[number]; color: string }[] {
  return MOOD_LEVELS.flatMap((level) => {
    const color = colors[level - 1];
    return color ? [{ level, color }] : [];
  });
}
