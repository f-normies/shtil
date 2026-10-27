import type { Schemas } from "@compass/api-client";
import { Alert, Button, Panel } from "@compass/ui";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { DefinitionSources } from "./AssessmentCatalog";
import { fetchAssessmentCatalog, fetchAssessmentHistory } from "./api";

type Assessment = Schemas["AssessmentOut"];
type Score = Assessment["scores"][string];

export function AssessmentHistory({ ownerId }: { ownerId: string }) {
  const [selectedScaleId, setSelectedScaleId] = useState("");
  const catalog = useQuery({
    queryKey: ["assessment-catalog", ownerId],
    queryFn: fetchAssessmentCatalog,
    staleTime: 0,
    retry: false,
  });
  const definitions = catalog.data ?? [];
  const selectedDefinition =
    definitions.find((definition) => definition.scale.id === selectedScaleId) ?? definitions[0];
  const scaleId = selectedDefinition?.scale.id;
  const history = useInfiniteQuery({
    queryKey: ["assessment-history", ownerId, scaleId],
    queryFn: ({ pageParam }) =>
      fetchAssessmentHistory({ ...(scaleId ? { scaleId } : {}), before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: catalog.isSuccess || catalog.isError,
    staleTime: 0,
    retry: false,
  });
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];
  const charts = buildCharts(items);

  return (
    <Panel title="Динамика">
      {catalog.isPending && <p className="text-sm text-ink-muted">Загружаем список опросников…</p>}
      {catalog.isError && (
        <Alert tone="error">
          Не удалось загрузить список опросников. Показана история всех ответов.
        </Alert>
      )}
      {definitions.length > 1 && (
        <label className="mb-4 flex flex-col gap-1 text-sm font-medium text-ink">
          Опросник для истории
          <select
            className="min-h-11 rounded-md border border-line bg-surface px-3 text-ink"
            value={selectedDefinition?.scale.id ?? ""}
            onChange={(event) => setSelectedScaleId(event.currentTarget.value)}
          >
            {definitions.map((definition) => (
              <option key={definition.scale.id} value={definition.scale.id}>
                {definition.scale.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {history.isPending && !catalog.isPending && (
        <p className="text-sm text-ink-muted">Загружаем результаты…</p>
      )}
      {history.isError && (
        <Alert tone="error">
          Не удалось загрузить историю. Проверьте связь и повторите попытку.
        </Alert>
      )}
      {history.isSuccess && items.length === 0 && (
        <p className="text-sm text-ink-muted">Подтверждённых результатов пока нет.</p>
      )}
      {charts.length > 0 && (
        <div className="mb-5 grid gap-3 sm:grid-cols-2">
          {charts.map((chart) => (
            <figure
              key={`${chart.scaleId}-${chart.version}-${chart.scoreId}`}
              className="min-w-0 rounded-md border border-line p-3"
            >
              <figcaption className="mb-2 text-sm font-medium text-ink">
                {chart.scaleTitle} · сумма {chart.scoreId}, версия {chart.version}
              </figcaption>
              <svg
                className="h-24 w-full"
                viewBox="0 0 240 80"
                role="img"
                aria-label={`График ${chart.scaleTitle}: сумма ${chart.scoreId}, версия ${chart.version}`}
              >
                <line x1="12" y1="68" x2="228" y2="68" stroke="currentColor" strokeOpacity="0.25" />
                {chart.points.length > 1 && (
                  <polyline
                    points={chart.points.map((point) => `${point.x},${point.y}`).join(" ")}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    className="text-primary"
                  />
                )}
                {chart.points.map((point) => (
                  <circle
                    key={`${point.assessmentId}-${point.value}`}
                    cx={point.x}
                    cy={point.y}
                    r="3.5"
                    fill="currentColor"
                    className="text-primary"
                  />
                ))}
              </svg>
            </figure>
          ))}
        </div>
      )}
      {items.length > 0 && (
        <>
          <div className="mb-4 flex flex-col gap-3">
            {items.map((item) => (
              <article key={item.id} className="rounded-md border border-line p-3">
                <h3 className="font-semibold text-ink">Версия {item.scale_version}</h3>
                <p className="text-sm text-ink-muted">{item.scale_title}</p>
                <DefinitionSources sources={item.sources} />
              </article>
            ))}
          </div>
          <div className="overflow-x-auto">
            <table
              className="w-full border-collapse text-left text-sm"
              aria-label="Значения сумм по результатам"
            >
              <thead>
                <tr className="border-b border-line text-ink">
                  <th scope="col" className="p-2">
                    Дата заполнения
                  </th>
                  <th scope="col" className="p-2">
                    Дата получения сервером
                  </th>
                  <th scope="col" className="p-2">
                    Сумма
                  </th>
                  <th scope="col" className="p-2">
                    Баллы
                  </th>
                  <th scope="col" className="p-2">
                    Интерпретация и источник порога
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.flatMap((item) =>
                  Object.entries(item.scores).map(([scoreId, score]) => (
                    <tr key={`${item.id}-${scoreId}`} className="border-b border-line align-top">
                      <td className="p-2 whitespace-nowrap">{formatDate(item.completed_at)}</td>
                      <td className="p-2 whitespace-nowrap">{formatDate(item.received_at)}</td>
                      <th scope="row" className="p-2 font-medium">
                        {scoreId}
                      </th>
                      <td className="p-2">{score.value}</td>
                      <td className="p-2">
                        <span>{score.band.label}</span>
                        <span className="mx-1">·</span>
                        <a
                          className="underline underline-offset-2"
                          href={score.band.source.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {score.band.source.title}
                        </a>
                        {score.band.source.supplementary && (
                          <span className="ml-1 font-medium">
                            Международный, дополнительный источник
                          </span>
                        )}
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
      {history.hasNextPage && (
        <Button
          className="mt-4"
          variant="secondary"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
        >
          {history.isFetchingNextPage ? "Загружаем…" : "Загрузить ещё"}
        </Button>
      )}
    </Panel>
  );
}

type Chart = {
  scaleId: string;
  scaleTitle: string;
  scoreId: string;
  version: number;
  points: { assessmentId: string; x: number; y: number; value: number }[];
};

function buildCharts(items: Assessment[]): Chart[] {
  const groups = new Map<
    string,
    {
      scaleId: string;
      scaleTitle: string;
      version: number;
      scoreId: string;
      values: { assessment: Assessment; score: Score }[];
    }
  >();
  for (const assessment of items) {
    for (const [scoreId, score] of Object.entries(assessment.scores)) {
      const key = `${assessment.scale_id}:${assessment.scale_version}:${scoreId}`;
      const group = groups.get(key) ?? {
        scaleId: assessment.scale_id,
        scaleTitle: assessment.scale_title,
        version: assessment.scale_version,
        scoreId,
        values: [],
      };
      group.values.push({ assessment, score });
      groups.set(key, group);
    }
  }

  return [...groups.values()].map((group) => {
    const ordered = [...group.values].sort(
      (left, right) =>
        Date.parse(left.assessment.completed_at) - Date.parse(right.assessment.completed_at),
    );
    const values = ordered.map(({ score }) => score.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    return {
      scaleId: group.scaleId,
      scaleTitle: group.scaleTitle,
      scoreId: group.scoreId,
      version: group.version,
      points: ordered.map(({ assessment, score }, index) => ({
        assessmentId: assessment.id,
        value: score.value,
        x: ordered.length === 1 ? 120 : 12 + (216 * index) / (ordered.length - 1),
        y: max === min ? 42 : 68 - ((score.value - min) / (max - min)) * 52,
      })),
    };
  });
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}
