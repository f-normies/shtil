import { createFileRoute } from "@tanstack/react-router";
import {
  axisDomain,
  definitionErrorText,
  groupByScale,
  outdated,
  scoreIds,
  zones,
} from "../../../../../card/chart";
import { scaleDomain, shortScaleName } from "../../../../../card/levels";
import {
  useAssessmentHistory,
  usePatient,
  useScaleDefinitionStates,
  useScaleSchedule,
} from "../../../../../card/queries";
import { commonPeriod } from "../../../../../card/surveys";
import { buildTrack, missedOf, windowOf } from "../../../../../card/track";
import { ClinicianScales } from "../../../../../components/scales/ClinicianScales";
import { PatientSurveys } from "../../../../../components/scales/PatientSurveys";
import { ScaleTrack } from "../../../../../components/scales/ScaleTrack";
import { Section } from "../../../../../components/signals/Section";

/**
 * Раздел «Шкалы» карточки (спека #13, раздел 10.4; кадры «Врач v4 · 09», «09б», «09в»):
 * графики шкал пациента с общей осью дат и пропусками по расписанию (#111), ниже —
 * опросы пациента (#180), шкалы врача строками. Кнопки «Таблицей» и процента от первого
 * результата нет.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/scales/")({
  component: Scales,
});

function Scales() {
  const { patientId } = Route.useParams();
  const patient = usePatient(patientId);
  const history = useAssessmentHistory(patientId);
  const schedule = useScaleSchedule(patientId);
  const groups = groupByScale(history.data ?? []);
  const plans = schedule.data?.items ?? [];
  const ids = [...new Set([...groups.map((g) => g.scaleId), ...plans.map((p) => p.scale_id)])];
  const definitions = useScaleDefinitionStates(ids);
  const now = new Date();
  const female = patient.data?.sex === "female";

  const tracks = ids
    .filter((id) => {
      const respondent = definitions.get(id)?.data?.scale.respondent;
      // Без определения шкала остаётся, если её заполнял пациент.
      return respondent
        ? respondent === "patient"
        : groups.some((g) => g.scaleId === id && g.results.some((r) => !r.recorded_by));
    })
    .map((id) => {
      const definition = definitions.get(id)?.data;
      const results = groups.find((g) => g.scaleId === id)?.results ?? [];
      const scoreId = scoreIds(results, definition)[0] ?? "total";
      const bands = zones(definition, scoreId);
      const missed = missedOf(plans, id);
      const title = definition?.scale.title ?? results.at(-1)?.scale_title ?? id;
      return {
        id,
        name: shortScaleName(title),
        domain: scaleDomain(id),
        definition,
        definitionFailed: definitions.get(id)?.isError ?? false,
        bands,
        slots: buildTrack({ results, missed, scoreId, bands }),
        outdated: outdated(results, definition),
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));

  // Общая ось дат — по окнам рисунка обеих шкал, не короче периода расписания (D3-3).
  const times = tracks.flatMap((t) => windowOf(t.slots).map((s) => Date.parse(s.at)));
  const axis = axisDomain(times) ?? { from: 0, to: 0 };
  const failed = tracks.filter((t) => t.definitionFailed);
  const retryDefinitions = () => {
    for (const t of failed) void definitions.get(t.id)?.refetch();
  };
  // У шкал может быть разный период пациента (#180): подпись — только при общем.
  const period = commonPeriod(plans);
  // Графики — когда есть и история, и расписание: иначе пропуски появлялись бы позже точек.
  const loading = history.isPending || (schedule.isPending && !schedule.isError);
  const ready = history.isSuccess && !loading;

  return (
    <>
      <Section title={female ? "Шкалы пациентки" : "Шкалы пациента"}>
        {loading && !history.isError && (
          <div aria-busy className="flex animate-pulse flex-col gap-3 motion-reduce:animate-none">
            <span className="h-4 w-40 rounded bg-surface-sunken" />
            <span className="h-10 w-56 rounded bg-surface-sunken" />
            <span className="h-32 rounded bg-surface-sunken" />
          </div>
        )}
        {history.isError && (
          <p className="flex items-center justify-between text-[15px] text-ink">
            Не удалось загрузить шкалы
            <button
              type="button"
              className="font-semibold text-primary-text"
              onClick={() => void history.refetch()}
            >
              Повторить
            </button>
          </p>
        )}
        {ready && schedule.isError && (
          <p className="mb-4 flex items-center justify-between text-[13px] text-ink-muted">
            Пропуски не показаны: не удалось загрузить расписание шкал
            <button
              type="button"
              className="font-semibold text-primary-text"
              onClick={() => void schedule.refetch()}
            >
              Повторить
            </button>
          </p>
        )}
        {ready && failed.length > 0 && (
          <p className="mb-4 flex items-center justify-between text-[13px] text-ink-muted">
            {definitionErrorText(failed.map((t) => t.name))}
            <button
              type="button"
              className="font-semibold text-primary-text"
              onClick={retryDefinitions}
            >
              Повторить
            </button>
          </p>
        )}
        {ready && tracks.length === 0 && (
          <p className="text-[15px] text-ink-muted">
            {female ? "Пациентка ещё не заполняла шкалы." : "Пациент ещё не заполнял шкалы."}
          </p>
        )}
        {ready && tracks.length > 0 && (
          <div className="flex flex-col gap-6 [&>*+*]:border-t [&>*+*]:border-separator [&>*+*]:pt-6">
            {period && (
              <p className="text-[13px] text-ink-muted">
                {female ? "Заполняет пациентка" : "Заполняет пациент"} раз в {period} дней
              </p>
            )}
            {tracks.map((t) => (
              <ScaleTrack
                key={t.id}
                name={t.name}
                scaleDomain={t.domain}
                bands={t.bands}
                history={t.slots}
                axis={axis}
                definition={t.definition}
                outdated={t.outdated}
                now={now}
              />
            ))}
          </div>
        )}
      </Section>
      <PatientSurveys patientId={patientId} />
      <ClinicianScales patientId={patientId} history={history.data ?? []} />
    </>
  );
}
