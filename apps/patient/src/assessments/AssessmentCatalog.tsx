import type { Schemas } from "@compass/api-client";
import { Alert, Panel } from "@compass/ui";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { getAssessmentStore, type PatientSession } from "../session";
import { fetchAssessmentCatalog } from "./api";

export function AssessmentCatalog({
  ownerId,
  session,
}: {
  ownerId: string;
  session: PatientSession;
}) {
  const online = session.status === "online";
  const catalog = useQuery({
    queryKey: ["assessment-catalog", ownerId],
    queryFn: async () => {
      const definitions = await fetchAssessmentCatalog();
      const store = await getAssessmentStore();
      for (const definition of definitions) await store.cacheDefinition(definition);
      return definitions;
    },
    enabled: online,
    staleTime: 0,
    retry: false,
  });
  const cached = useQuery({
    queryKey: ["assessment-cached-definitions", ownerId],
    queryFn: async () => (await getAssessmentStore()).getCachedDefinitions(),
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
  const fallbackDefinitions = latestDefinitions(cached.data ?? []);
  const definitions = online && !catalog.isError ? (catalog.data ?? []) : fallbackDefinitions;

  return (
    <Panel title="Опросники">
      {online && catalog.isPending && definitions.length === 0 && (
        <p className="text-sm text-ink-muted">Загружаем опросники…</p>
      )}
      {online && catalog.isError && definitions.length === 0 && (
        <Alert tone="error">
          Не удалось загрузить опросники. Проверьте связь и повторите попытку.
        </Alert>
      )}
      {online && catalog.isError && definitions.length > 0 && (
        <p role="status" className="mb-3 text-sm text-ink-muted">
          Связь потеряна. Показаны ранее загруженные версии.
        </p>
      )}
      {!online && cached.isPending && (
        <p className="text-sm text-ink-muted">Открываем сохранённые опросники…</p>
      )}
      {!online && cached.isError && (
        <Alert tone="error">Не удалось прочитать сохранённые опросники.</Alert>
      )}
      {((online && catalog.isSuccess) || !online || catalog.isError) &&
        definitions.length === 0 && (
          <p className="text-sm text-ink-muted">Опросники пока не доступны.</p>
        )}
      {definitions.length > 0 && (
        <ul className="flex flex-col gap-4">
          {definitions.map((definition) => (
            <li
              key={definition.scale.id}
              className="border-t border-line pt-4 first:border-0 first:pt-0"
            >
              <h3 className="font-semibold text-ink">{definition.scale.title}</h3>
              <p className="mt-1 text-sm text-ink-muted">{definition.scale.instruction}</p>
              <DefinitionSources sources={definition.sources} />
              <Link
                to="/assessments/$scaleId"
                params={{ scaleId: definition.scale.id }}
                className="mt-3 inline-flex min-h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Заполнить
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function latestDefinitions(
  definitions: Schemas["ScaleDefinitionOut"][],
): Schemas["ScaleDefinitionOut"][] {
  const byScale = new Map<string, Schemas["ScaleDefinitionOut"]>();
  for (const definition of definitions) {
    const current = byScale.get(definition.scale.id);
    if (!current || definition.scale.version > current.scale.version) {
      byScale.set(definition.scale.id, definition);
    }
  }
  return [...byScale.values()];
}

export function DefinitionSources({ sources }: { sources: Schemas["SourceOut"][] }) {
  if (sources.length === 0) return null;
  return (
    <ul className="mt-3 flex flex-col gap-1 text-sm text-ink-muted">
      {sources.map((source) => (
        <li key={source.id}>
          <a
            className="underline underline-offset-2"
            href={source.url}
            target="_blank"
            rel="noreferrer"
          >
            {source.title}
          </a>
          {source.supplementary && (
            <span className="ml-1 font-medium text-ink">
              Международный, дополнительный источник
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
