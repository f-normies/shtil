import mood1 from "./assets/mood-1.svg";
import mood2 from "./assets/mood-2.svg";
import mood3 from "./assets/mood-3.svg";
import mood4 from "./assets/mood-4.svg";
import mood5 from "./assets/mood-5.svg";

export const MOOD_LEVELS = [
  { level: 1, label: "Очень тяжело", image: mood1, color: "#77B2C6" },
  { level: 2, label: "Тяжело", image: mood2, color: "#A9D0DD" },
  { level: 3, label: "Ровно", image: mood3, color: "#86BFA9" },
  { level: 4, label: "Неплохо", image: mood4, color: "#F5E09A" },
  { level: 5, label: "Хорошо", image: mood5, color: "#F2A57C" },
] as const;

export const MOOD_WORDS = [
  "спокойствие",
  "радость",
  "интерес",
  "усталость",
  "тревога",
  "напряжение",
  "раздражение",
  "грусть",
  "одиночество",
  "плохой сон",
] as const;
export type MoodWord = (typeof MOOD_WORDS)[number];

export function moodLabel(level: number | null): string | null {
  return MOOD_LEVELS.find((item) => item.level === level)?.label ?? null;
}

export function MoodScale({
  selected,
  onSelect,
  context,
  compact = false,
  disabled = false,
}: {
  selected: number | null;
  onSelect: (level: number) => void;
  context: "home-daily" | "home-quick" | "daily" | "quick";
  compact?: boolean;
  disabled?: boolean;
}) {
  const levels = MOOD_LEVELS;
  return (
    <fieldset className={`patient-mood-scale${compact ? " patient-mood-scale--compact" : ""}`}>
      <legend className="sr-only">Как вы сейчас?</legend>
      <div className="patient-mood-scale__buttons">
        {levels.map(({ level, label, image, color }) => {
          const accessibleName =
            context === "home-daily"
              ? `Начать ежедневную отметку: ${label}`
              : context === "home-quick"
                ? `Быстрая отметка настроения: ${label}`
                : label;
          return (
            <button
              key={level}
              className="patient-mood-scale__button"
              type="button"
              aria-label={accessibleName}
              aria-pressed={selected === level}
              data-selected={selected === level || undefined}
              disabled={disabled}
              onClick={() => onSelect(level)}
            >
              {selected === level && context === "daily" ? (
                <span className="patient-mood-scale__check" aria-hidden="true">
                  <svg viewBox="0 0 64 64" focusable="false">
                    <title>Выбранное настроение</title>
                    <circle
                      cx="32"
                      cy="32"
                      r="30.75"
                      fill="none"
                      stroke="#1F5F77"
                      strokeWidth="2.5"
                    />
                    <circle cx="32" cy="32" r="26" fill={color} />
                    <path
                      d="M23.8333 32.5833L29.0833 37.8333L40.1667 26.1667"
                      fill="none"
                      stroke="#0C2834"
                      strokeWidth="2.25"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
              ) : (
                <img src={image} alt="" aria-hidden="true" />
              )}
              {context === "quick" && <span className="patient-mood-scale__label">{label}</span>}
            </button>
          );
        })}
      </div>
      {(context === "home-daily" || context === "home-quick" || context === "daily") && (
        <div className="patient-mood-scale__endpoints" aria-hidden="true">
          <span>Очень тяжело</span>
          <span>Хорошо</span>
        </div>
      )}
    </fieldset>
  );
}
