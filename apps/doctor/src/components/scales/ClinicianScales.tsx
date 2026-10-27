import type { Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import { Plus } from "lucide-react";
import { useState } from "react";
import { zones } from "../../card/chart";
import { scaleDomain, shortScaleName } from "../../card/levels";
import { useClinicianScales } from "../../card/queries";
import { shortDay } from "../../card/track";
import { EventList } from "../details/EventRow";
import { Section } from "../signals/Section";
import { ScaleForm } from "./ScaleForm";

/**
 * «Шкалы врача» (спека #13, раздел 10.4; кадр 09): HAMD-17 и HARS строками с последним
 * результатом. «Заполнить» открывает лист с формой интервью (#94).
 */
export function ClinicianScales({
  patientId,
  history,
}: {
  patientId: string;
  history: readonly Schemas["AssessmentOut"][];
}) {
  const catalog = useClinicianScales();
  const scales = catalog.data?.items ?? [];
  const [filling, setFilling] = useState<Schemas["Scale"] | null>(null);
  if (scales.length === 0) {
    return null;
  }
  return (
    <Section title="Шкалы врача" note="Заполняете на приёме." flush>
      <EventList>
        {scales.map(({ scale }) => {
          const scoreId = scale.scores[0]?.id ?? "total";
          const top = Math.max(0, ...zones({ scale, sources: [] }, scoreId).map((b) => b.max));
          const last = history
            .filter((r) => r.scale_id === scale.id)
            .sort((a, b) => b.completed_at.localeCompare(a.completed_at))[0];
          const score = last?.scores[scoreId];
          return (
            <li key={scale.id} className="flex items-center gap-4 px-4 py-3">
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-[15px] font-semibold text-ink">
                  {shortScaleName(scale.title)}
                </span>
                <span className="text-[13px] text-ink-muted">
                  {scaleDomain(scale.id) ? `${scaleDomain(scale.id)}, ` : ""}шкала 0–{top},{" "}
                  {last && score
                    ? `последний результат ${score.value} из ${top}, ${score.band.label}, ${shortDay(last.completed_at)}`
                    : "ещё не заполняли"}
                </span>
              </span>
              <Button
                variant="secondary"
                aria-label={`Заполнить ${shortScaleName(scale.title)}`}
                onClick={() => setFilling(scale)}
              >
                <Plus aria-hidden className="size-4" />
                Заполнить
              </Button>
            </li>
          );
        })}
      </EventList>
      {filling && (
        <ScaleForm
          key={`${filling.id}:${filling.version}`}
          scale={filling}
          patientId={patientId}
          onClose={() => setFilling(null)}
          onReload={async () => {
            const latest = await catalog.refetch();
            const next = latest.data?.items.find(({ scale }) => scale.id === filling.id)?.scale;
            if (latest.error || !next || next.version === filling.version) {
              throw new Error("New scale version is unavailable");
            }
            setFilling(next);
          }}
        />
      )}
    </Section>
  );
}
