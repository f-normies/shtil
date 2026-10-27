import moonIcon from "@compass/ui/assets/ikonka-luna.svg";
import { ConsentIcon } from "../consent/ConsentIcon";
import { showNightMoon } from "./onboarding-flow";

type Props = {
  value: string;
  onChange: (value: string) => void;
  mode: "onboarding" | "profile";
  disabled?: boolean;
};

function parseTime(value: string): [number, number] {
  const hour = Number(value.slice(0, 2));
  const minute = Number(value.slice(3, 5));
  return [Number.isInteger(hour) ? hour : 21, Number.isInteger(minute) ? minute : 0];
}

function formatTime(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function deviceInstructions(): string {
  if (typeof navigator === "undefined")
    return "В меню браузера выберите «Добавить на главный экран».";
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) {
    return "Откройте меню «Поделиться» и выберите «На экран Домой».";
  }
  if (/Android/i.test(navigator.userAgent)) {
    return "Откройте меню браузера и выберите «Установить приложение» или «Добавить на главный экран».";
  }
  return "В меню браузера выберите «Установить приложение» или «Добавить на главный экран».";
}

export function TimePicker({ value, onChange, mode, disabled = false }: Props) {
  const [hour, minute] = parseTime(value);
  // Луна с 00:00 до 04:59 — одна и та же в онбординге (06б) и в профиле (28); сохранить можно.
  const moonVisible = showNightMoon(value);
  const moonText =
    mode === "profile"
      ? "Если ночь — ваше время, оставим так."
      : "Пусть в это время приходят только сны.";
  const nextHour = (delta: number) => onChange(formatTime((hour + delta + 24) % 24, minute));
  const nextMinute = (delta: number) => {
    const next = ((minute / 15 + delta + 96) % 96) % 4;
    const hourDelta = Math.floor((minute / 15 + delta) / 4);
    onChange(formatTime((hour + hourDelta + 24) % 24, next * 15));
  };

  return (
    <div className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-medium text-ink">Время ежедневной отметки</legend>
        <div className="flex items-center justify-center gap-4 rounded-2xl border border-line bg-surface px-4 py-5">
          <WheelColumn
            label="Часы"
            value={hour}
            format={(item) => String(item).padStart(2, "0")}
            onPrevious={() => nextHour(-1)}
            onNext={() => nextHour(1)}
            onKeyStep={nextHour}
            min={0}
            max={23}
            disabled={disabled}
          />
          <span aria-hidden="true" className="pb-1 text-3xl font-semibold text-ink">
            :
          </span>
          <WheelColumn
            label="Минуты"
            value={minute}
            format={(item) => String(item).padStart(2, "0")}
            onPrevious={() => nextMinute(-1)}
            onNext={() => nextMinute(1)}
            onKeyStep={nextMinute}
            min={0}
            max={45}
            disabled={disabled}
          />
        </div>
        <p className="text-sm text-ink-muted">Время по Москве: {formatTime(hour, minute)}.</p>
        <div role="status" aria-live="polite">
          {moonVisible && (
            <p
              className="flex items-center gap-2.5 rounded-2xl bg-primary-soft py-2.5 pl-3.5 pr-4 text-[15px] leading-5 text-primary-text"
              data-testid="night-moon"
            >
              <ConsentIcon src={moonIcon} />
              <span>
                <span className="sr-only">Ночное время. </span>
                {moonText}
              </span>
            </p>
          )}
        </div>
      </fieldset>
      <fieldset className="flex flex-wrap gap-2" aria-label="Быстрое время">
        {["09:00", "14:00", "21:00"].map((time) => (
          <button
            key={time}
            type="button"
            aria-pressed={value === time}
            disabled={disabled}
            onClick={() => onChange(time)}
            className={`min-h-10 rounded-full border px-4 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${value === time ? "border-primary bg-accent-calm-soft text-ink" : "border-line bg-surface text-ink-muted"}`}
          >
            {time}
          </button>
        ))}
      </fieldset>
      <div className="rounded-xl bg-canvas p-3 text-sm text-ink-muted">
        <span className="font-medium text-ink">Предпросмотр настройки:</span> ежедневная отметка —{" "}
        {formatTime(hour, minute)}.
      </div>
      <details className="rounded-xl border border-line bg-surface px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium text-primary">
          Как добавить приложение на экран
        </summary>
        <p className="mt-2 text-ink-muted">{deviceInstructions()}</p>
        <p className="mt-2 text-xs text-ink-muted">
          Это только подсказка. Уведомления в приложении пока не отправляются.
        </p>
      </details>
    </div>
  );
}

function WheelColumn({
  label,
  value,
  format,
  onPrevious,
  onNext,
  onKeyStep,
  min,
  max,
  disabled = false,
}: {
  label: string;
  value: number;
  format: (value: number) => string;
  onPrevious: () => void;
  onNext: () => void;
  onKeyStep: (delta: number) => void;
  min: number;
  max: number;
  disabled?: boolean;
}) {
  return (
    <div className="flex w-20 flex-col items-center gap-1">
      <span className="text-xs text-ink-muted">{label}</span>
      <button
        type="button"
        disabled={disabled}
        aria-label={`Предыдущее значение: ${label.toLocaleLowerCase()}`}
        onClick={onPrevious}
        className="grid size-9 place-items-center rounded-full text-xl text-ink-muted hover:bg-canvas focus-visible:outline-2 focus-visible:outline-primary"
      >
        −
      </button>
      <div
        role="spinbutton"
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={value}
        tabIndex={0}
        onKeyDown={(event) => {
          if (disabled) return;
          if (event.key === "ArrowUp" || event.key === "ArrowRight") {
            event.preventDefault();
            onKeyStep(1);
          }
          if (event.key === "ArrowDown" || event.key === "ArrowLeft") {
            event.preventDefault();
            onKeyStep(-1);
          }
        }}
        className="min-w-14 rounded-lg px-2 py-1 text-center text-3xl font-semibold tabular-nums text-ink outline-none focus-visible:ring-2 focus-visible:ring-primary"
        aria-disabled={disabled}
        onClick={(event) => {
          if (disabled) event.currentTarget.blur();
        }}
      >
        {format(value)}
      </div>
      <button
        type="button"
        disabled={disabled}
        aria-label={`Следующее значение: ${label.toLocaleLowerCase()}`}
        onClick={onNext}
        className="grid size-9 place-items-center rounded-full text-xl text-ink-muted hover:bg-canvas focus-visible:outline-2 focus-visible:outline-primary"
      >
        +
      </button>
    </div>
  );
}
