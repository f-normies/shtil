import type { Schemas } from "@compass/api-client";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { indicatorText, percentOf } from "../../card/adherence";
import { groupByScale, scoreIds, zones } from "../../card/chart";
import { type BandRange, monotonePath, shortScaleName } from "../../card/levels";
import { usePatientAdherence, useScaleDefinitions, useScaleSchedule } from "../../card/queries";
import {
  buildTrack,
  missedOf,
  type PointSlot,
  pieces,
  type Slot,
  slotY,
  trackTrend,
  windowOf,
} from "../../card/track";
import { Section } from "../signals/Section";

const SPARK = { width: 120, height: 44, pad: 5 };

/**
 * «Состояние» (спека #13, разделы 10.2 и 10.3): по строке «Показатель/Шкала» на каждую
 * заполненную шкалу — градация словами, компактные «Уровни шкалы» с пропусками по
 * расписанию (#111) и последний балл; строка «Приём» — доля доз за 14 дней (#34).
 */
export function StateSection({
  patientId,
  history,
  allowAdherence = true,
}: {
  patientId: string;
  history: UseQueryResult<Schemas["AssessmentOut"][]>;
  allowAdherence?: boolean;
}) {
  const groups = groupByScale(history.data ?? []);
  const schedule = useScaleSchedule(patientId);
  const plans = schedule.data?.items ?? [];
  const definitions = useScaleDefinitions(groups.map((g) => g.scaleId));
  const adherence = usePatientAdherence(patientId, allowAdherence);
  const now = new Date();

  return (
    <Section title="Состояние" flush>
      {history.isPending && <p className="px-4 py-3 text-sm text-ink-muted">Загрузка…</p>}
      {history.isError && (
        <p className="flex items-center justify-between px-4 py-3 text-sm text-danger">
          Не удалось загрузить шкалы.
          <button
            type="button"
            className="font-semibold text-primary-text"
            onClick={() => void history.refetch()}
          >
            Повторить
          </button>
        </p>
      )}
      {history.isSuccess && schedule.isError && (
        <p className="flex items-center justify-between px-4 py-3 text-sm text-ink-muted">
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
      {history.isSuccess && groups.length === 0 && (
        <p className="px-4 py-3 text-sm text-ink-muted">Пациент ещё не заполнял шкалы.</p>
      )}
      <ul className="flex flex-col [&>li+li]:border-t [&>li+li]:border-separator">
        {groups.map((g) => {
          const definition = definitions.get(g.scaleId);
          const scoreId = scoreIds(g.results, definition)[0];
          if (!scoreId) {
            return null;
          }
          const bands = zones(definition, scoreId);
          const missed = missedOf(plans, g.scaleId);
          const slots = buildTrack({ results: g.results, missed, scoreId, bands });
          return (
            <li key={g.scaleId}>
              <ScaleIndicator
                name={shortScaleName(g.title)}
                slots={slots}
                bands={bands}
                trend={trackTrend(slots, now)}
              />
            </li>
          );
        })}
        {allowAdherence && adherence.data?.period && adherence.data.course !== "none" && (
          <li>
            <AdherenceIndicator patientId={patientId} adherence={adherence.data} />
          </li>
        )}
      </ul>
    </Section>
  );
}

/** «Показатель/Приём»: доля доз за 14 дней по отметкам и переход в раздел «Приём». */
function AdherenceIndicator({
  patientId,
  adherence,
}: {
  patientId: string;
  adherence: Schemas["AdherenceOut"];
}) {
  const percent = percentOf(adherence);
  return (
    <Link
      to="/patients/$patientId/adherence"
      params={{ patientId }}
      className="flex min-h-16 items-center gap-4 px-4 py-2 outline-none focus-visible:ring-2 focus-visible:ring-primary"
    >
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-5 font-semibold text-ink">Приём</p>
        <p className="text-[13px] leading-[18px] text-ink-muted">{indicatorText(adherence)}</p>
      </div>
      {percent !== null && (
        <span className="text-3xl leading-9 font-semibold text-ink">{percent}%</span>
      )}
      <ChevronRight aria-hidden className="size-5 text-ink-muted" />
    </Link>
  );
}

/** «Показатель/Шкала»: название и смена градации, график, крупный балл «6 из 27». */
function ScaleIndicator({
  name,
  slots,
  bands,
  trend,
}: {
  name: string;
  slots: Slot[];
  bands: BandRange[];
  trend: string;
}) {
  const last = slots.findLast((s): s is PointSlot => s.kind === "point");
  const max = bands.length > 0 ? Math.max(...bands.map((b) => b.max)) : null;
  return (
    <div className="flex min-h-16 items-center gap-4 px-4 py-2">
      <div className="min-w-0 flex-1">
        <p className="text-[15px] leading-5 font-semibold text-ink">{name}</p>
        <p className="text-[13px] leading-[18px] text-ink-muted">{trend}</p>
      </div>
      {bands.length > 0 && <LevelsSpark slots={windowOf(slots)} lanes={bands.length} />}
      <p className="flex w-24 items-baseline justify-end gap-1">
        {last ? (
          <>
            <span className="text-3xl leading-9 font-semibold text-ink">{last.value}</span>
            {max !== null && <span className="text-[13px] text-ink-muted">из {max}</span>}
          </>
        ) : (
          <span className="text-sm text-ink-muted">Нет данных</span>
        )}
      </p>
    </div>
  );
}

/**
 * Компактные «Уровни шкалы» 120×44: дорожки градаций, тяжелее выше, точки на дорожках
 * своих градаций, монотонная кривая и пунктир через пропуск с пустым кружком в дату
 * пропуска. Точки стоят по порядку плановых измерений. Декоративный: значение и градация
 * написаны текстом. Цвета нейтральные, красный и янтарный — только у сигналов.
 */
function LevelsSpark({ slots, lanes }: { slots: Slot[]; lanes: number }) {
  const innerW = SPARK.width - 2 * SPARK.pad;
  const step = lanes > 1 ? (SPARK.height - 2 * SPARK.pad) / (lanes - 1) : 0;
  const y = (lane: number) => SPARK.height - SPARK.pad - lane * step;
  const order = new Map(slots.map((s, i) => [s.at, i]));
  const x = (at: string) =>
    SPARK.pad + (slots.length > 1 ? ((order.get(at) ?? 0) / (slots.length - 1)) * innerW : innerW);
  const xy = slots.map((s, i) => ({ x: x(s.at), y: slotY(slots, i, x, y) }));
  const at = (s: Slot) => xy[slots.indexOf(s)] ?? { x: 0, y: 0 };
  const { solid, dashed } = pieces(slots);
  const last = slots.findLast((s) => s.kind === "point");
  return (
    <svg
      aria-hidden
      width={SPARK.width}
      height={SPARK.height}
      viewBox={`0 0 ${SPARK.width} ${SPARK.height}`}
      className="shrink-0"
    >
      {Array.from({ length: lanes }, (_, lane) => y(lane)).map((ly) => (
        <line key={ly} x1={0} x2={SPARK.width} y1={ly} y2={ly} className="stroke-line" />
      ))}
      {solid.map((run) => (
        <path
          key={run[0]?.at}
          d={monotonePath(run.map(at))}
          fill="none"
          className="stroke-primary"
          strokeWidth={1.5}
        />
      ))}
      {dashed.map(([a, b]) => (
        <line
          key={`${a.at}-${b.at}`}
          x1={at(a).x}
          y1={at(a).y}
          x2={at(b).x}
          y2={at(b).y}
          className="stroke-primary"
          strokeWidth={1.5}
          strokeDasharray="2 3"
        />
      ))}
      {slots.map((s, i) => {
        const p = xy[i] ?? { x: 0, y: 0 };
        return s.kind === "gap" ? (
          <circle
            key={s.at}
            cx={p.x}
            cy={p.y}
            r={2.5}
            className="fill-surface stroke-primary"
            strokeWidth={1.5}
          />
        ) : (
          <circle key={s.at} cx={p.x} cy={p.y} r={s === last ? 3.5 : 2} className="fill-primary" />
        );
      })}
    </svg>
  );
}
