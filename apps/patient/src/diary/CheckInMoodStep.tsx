import { Button } from "@compass/ui";
import { useEffect, useState } from "react";
import pebble1 from "./assets/pebble-mood-1.svg";
import pebble2 from "./assets/pebble-mood-2.svg";
import pebble3 from "./assets/pebble-mood-3.svg";
import pebble4 from "./assets/pebble-mood-4.svg";
import pebble5 from "./assets/pebble-mood-5.svg";
import { MoodScale, moodLabel } from "./MoodScale";

const pebbleByMood = [pebble1, pebble2, pebble3, pebble4, pebble5] as const;

export function CheckInMoodStep({
  level,
  onSelect,
  onNext,
}: {
  level: number | null;
  onSelect: (level: number) => void;
  onNext: () => void;
}) {
  const [breathing, setBreathing] = useState(false);
  useEffect(() => {
    if (level === null) return;
    const timer = window.setTimeout(() => setBreathing(true), 6_000);
    return () => window.clearTimeout(timer);
  }, [level]);
  const pebble = level === null ? pebble3 : (pebbleByMood[level - 1] ?? pebble3);
  return (
    <>
      <div className="patient-checkin-main patient-checkin-main--mood">
        <h1 className="type-patient-question">Как вы сейчас?</h1>
        <p className="type-patient-body patient-checkin-subtitle">
          Выберите, что ближе. Правильного ответа нет.
        </p>
        <div
          className={`patient-pebble${level ? " patient-pebble--selected" : ""}${breathing ? " patient-pebble--breathing" : ""}`}
          aria-hidden="true"
          onPointerDown={() => setBreathing(false)}
        >
          <img src={pebble} alt="" />
        </div>
        <MoodScale
          context="daily"
          selected={level}
          onSelect={(next) => {
            setBreathing(false);
            onSelect(next);
          }}
        />
        <p className="patient-selected-mood" aria-live="polite">
          {moodLabel(level) ?? "Выберите настроение"}
        </p>
      </div>
      <div className="patient-checkin-actions">
        <Button className="patient-checkin-primary" disabled={level === null} onClick={onNext}>
          Дальше
        </Button>
      </div>
    </>
  );
}
