import type { Schemas } from "@compass/api-client";
import { type IntakeMark, intakeLine } from "./doses";
import "./intake-row.css";

type Prescription = Schemas["ScheduleItem"];

/**
 * Строка приёма «Пациент/Строка лекарства» (кадры 12, 13, 16): таблетка, «Авиандр 20 мг»,
 * «Утром, принято в 08:40», квадрат отметки с зоной касания 44 px. Общая для главной
 * и шага «Лекарства» ежедневной отметки.
 */
export function IntakeRow({
  prescription,
  ordinal,
  mark,
  onToggle,
}: {
  prescription: Prescription;
  ordinal: number;
  mark: IntakeMark | undefined;
  onToggle: () => void;
}) {
  const taken = mark?.status === "taken";
  const title = `${prescription.trade_name} ${prescription.dose_mg} мг`;
  const line = intakeLine(prescription, ordinal, mark);
  return (
    <div className="patient-intake-row">
      <span className="patient-medicine-glyph" aria-hidden="true">
        <i />
      </span>
      <div className="patient-intake-row__text">
        <strong>{title}</strong>
        <span data-taken={taken || undefined}>{line}</span>
      </div>
      <button
        type="button"
        className="patient-dose-check"
        data-checked={taken || undefined}
        aria-pressed={taken}
        aria-label={`${title}, ${line.toLowerCase()}: ${taken ? "снять отметку" : "отметить приём"}`}
        onClick={onToggle}
      >
        <span aria-hidden="true">
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={3}
          >
            <path d="M5 12.5l4.5 4.5L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>
    </div>
  );
}

/** Инструкции ОХЛП один раз на карточку, свёрнуто (P2-05): в строках приёма их нет. */
export function IntakeInstructions({ prescriptions }: { prescriptions: readonly Prescription[] }) {
  const withInstructions = prescriptions.filter((item) => item.instructions.length > 0);
  if (withInstructions.length === 0) return null;
  return (
    <details className="patient-intake-instructions">
      <summary>Как принимать</summary>
      {withInstructions.map((item) => (
        <div key={item.prescription_id}>
          {withInstructions.length > 1 && <strong>{item.trade_name}</strong>}
          <ul>
            {item.instructions.map((instruction) => (
              <li key={instruction}>{instruction}</li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}
