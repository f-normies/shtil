import type { Schemas } from "@compass/api-client";
import { Checkbox, SegmentedControl } from "@compass/ui";
import { shortScaleName } from "../../card/levels";
import { useSaveScaleSetting, useScaleSettings } from "../../card/surveyQueries";
import { changedText, periodLabel } from "../../card/surveys";
import { EventList } from "../details/EventRow";
import { Section } from "../signals/Section";

type Setting = Schemas["ScaleSettingOut"];

/**
 * «Опросы пациента» (спека #180): врач включает шкалу самоотчёта и выбирает частоту из
 * допустимых периодов `knowledge/scale-schedule.yaml`. Вопросы и подсчёт не меняются.
 */
export function PatientSurveys({ patientId }: { patientId: string }) {
  const settings = useScaleSettings(patientId);
  const save = useSaveScaleSetting();
  const items = settings.data?.items ?? [];
  const allowed = settings.data?.allowed_period_days ?? [];

  if (settings.isError) {
    return (
      <Section title="Опросы пациента">
        <p className="flex items-center justify-between text-[15px] text-ink">
          Не удалось загрузить опросы
          <button
            type="button"
            className="font-semibold text-primary-text"
            onClick={() => void settings.refetch()}
          >
            Повторить
          </button>
        </p>
      </Section>
    );
  }
  if (items.length === 0) {
    return null;
  }

  const put = (item: Setting, body: { enabled: boolean; period_days: number }) =>
    save.mutate({
      params: { path: { patient_id: patientId, scale_id: item.scale_id } },
      body,
    });

  return (
    <Section title="Опросы пациента" note="Пациент заполняет шкалы в приложении." flush>
      <EventList>
        {items.map((item) => {
          const name = shortScaleName(item.scale_title);
          return (
            <li
              key={item.scale_id}
              className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3"
            >
              <span className="flex min-w-40 flex-1 flex-col">
                <span className="text-[15px] font-semibold text-ink">{name}</span>
                <span className="text-[13px] text-ink-muted">{changedText(item)}</span>
              </span>
              <Checkbox
                checked={item.enabled}
                disabled={save.isPending}
                aria-label={`Опрашивать ${name}`}
                onChange={(event) =>
                  put(item, { enabled: event.target.checked, period_days: item.period_days })
                }
              >
                Опрашивать
              </Checkbox>
              <SegmentedControl
                ariaLabel={`Частота ${name}`}
                options={allowed.map((days) => ({
                  value: String(days),
                  label: periodLabel(days),
                  disabled: !item.enabled || save.isPending,
                }))}
                value={String(item.period_days)}
                onValueChange={(value) =>
                  put(item, { enabled: item.enabled, period_days: Number(value) })
                }
              />
            </li>
          );
        })}
      </EventList>
      {save.isError && (
        <p role="alert" className="px-4 text-[13px] text-danger">
          Не удалось сохранить. Попробуйте ещё раз.
        </p>
      )}
    </Section>
  );
}
