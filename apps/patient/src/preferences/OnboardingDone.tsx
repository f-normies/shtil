import type { Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import micIcon from "@compass/ui/assets/ikonka-mikrofon.svg";
import scalesIcon from "@compass/ui/assets/ikonka-shkaly.svg";
import textIcon from "@compass/ui/assets/ikonka-tekst.svg";
import therapyIcon from "@compass/ui/assets/ikonka-terapiya.svg";
import timeIcon from "@compass/ui/assets/ikonka-vremya.svg";
import { SummaryRow } from "./OnboardingFrame";
import {
  type EditablePreferences,
  firstCheckInText,
  MOOD_PALETTES,
  moodLevelColors,
} from "./onboarding-flow";
import { describeAppearance } from "./pill-appearance";

function localTime(timezone: string): string {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: timezone,
  }).format(new Date());
}

/** Последний шаг онбординга «Всё готово» (кадр «Пациент · 11»): сводка выбранного и выход на «Сегодня». */
export function OnboardingDone({
  draft,
  timezone,
  prescriptions,
  busy,
  saving,
  onOpenToday,
}: {
  draft: EditablePreferences;
  timezone: string;
  prescriptions: Schemas["ScheduleItem"][];
  busy: boolean;
  saving: boolean;
  onOpenToday: () => void;
}) {
  const palette = MOOD_PALETTES.find((item) => item.value === draft.mood_palette);
  const name = draft.display_name.trim();
  return (
    <div className="flex flex-1 flex-col gap-5">
      <div className="flex flex-col gap-3">
        <h1 className="text-[30px] font-semibold leading-9 tracking-tight">
          {name ? `Всё готово, ${name}` : "Всё готово"}
        </h1>
        <p className="text-[17px] leading-6 text-ink-muted">
          {firstCheckInText(draft.reminder_time, localTime(timezone))} Пришлём тихое напоминание.
        </p>
      </div>
      <dl className="m-0 flex flex-col rounded-3xl bg-surface px-4 shadow-sm">
        <SummaryRow
          icon={timeIcon}
          label="Отметка"
          value={`Каждый день в ${draft.reminder_time}`}
        />
        <SummaryRow
          icon={scalesIcon}
          label="Цвета настроения"
          value={
            <span role="img" aria-label={palette?.title} className="flex items-center gap-2">
              {moodLevelColors(palette?.colors ?? []).map(({ level, color }) => (
                <span
                  key={level}
                  className="block size-4 rounded-full"
                  style={{ backgroundColor: color }}
                />
              ))}
            </span>
          }
        />
        <SummaryRow
          icon={draft.story_mode === "voice" ? micIcon : textIcon}
          label="Рассказ о дне"
          value={draft.story_mode === "voice" ? "Голосом" : "Текстом"}
          last={prescriptions.length === 0}
        />
        {prescriptions.map((item, index) => (
          <SummaryRow
            key={item.prescription_id}
            icon={therapyIcon}
            label={prescriptions.length > 1 ? item.trade_name : "Таблетка"}
            value={describeAppearance(item.appearance ?? item.default_appearance)}
            last={index === prescriptions.length - 1}
          />
        ))}
      </dl>
      <div className="mt-auto flex flex-col items-center gap-3 pt-3">
        <p className="text-[13px] font-medium leading-[18px] text-ink-muted">
          Всё это можно поменять в профиле.
        </p>
        <Button className="w-full rounded-full" onClick={onOpenToday} disabled={busy}>
          {saving ? "Сохраняем…" : "Открыть «Сегодня»"}
        </Button>
      </div>
    </div>
  );
}
