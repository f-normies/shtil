import backIcon from "@compass/ui/assets/ikonka-vlevo.svg";
import closeIcon from "@compass/ui/assets/ikonka-zakryt.svg";
import type { DiaryDraftStep } from "./types";

const STEP_LABELS: Record<DiaryDraftStep, string> = {
  mood: "Настроение",
  story: "О дне",
  intake: "Лекарства",
  survey: "Опрос",
  complete: "Готово",
};

/** Fixed geometry used by each daily check-in step. */
export function CheckInHeader({
  step,
  index,
  count,
  onBack,
  onClose,
  disabled = false,
}: {
  step: DiaryDraftStep;
  index: number;
  count: number;
  onBack?: () => void;
  onClose: () => void;
  disabled?: boolean;
}) {
  return (
    <header className="patient-checkin-header">
      <div className="patient-checkin-header__top">
        <button
          type="button"
          className="patient-checkin-header__edge"
          aria-label={onBack ? "Назад" : "Закрыть отметку"}
          disabled={disabled}
          onClick={onBack ?? onClose}
        >
          <img src={onBack ? backIcon : closeIcon} alt="" />
        </button>
        <span>{STEP_LABELS[step]}</span>
        <span className="patient-checkin-header__count">
          {index} из {count}
        </span>
      </div>
      <div
        className="patient-checkin-header__progress"
        role="progressbar"
        aria-label="Прогресс ежедневной отметки"
        aria-valuemin={1}
        aria-valuemax={count}
        aria-valuenow={index}
        aria-valuetext={`Шаг ${index} из ${count}`}
        style={{ gridTemplateColumns: `repeat(${count}, minmax(0, 1fr))` }}
      >
        {Array.from({ length: count }, (_, part) => `step-${part + 1}`).map((key, part) => (
          <i key={key} data-complete={part < index || undefined} />
        ))}
      </div>
    </header>
  );
}
