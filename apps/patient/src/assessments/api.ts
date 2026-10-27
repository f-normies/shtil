import { fetchClient, type Schemas } from "@compass/api-client";

export class AssessmentApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "AssessmentApiError";
  }
}

function requireData<T>(data: T | undefined, response: Response, error: unknown): T {
  if (data) return data;
  const problem =
    error && typeof error === "object"
      ? (error as { title?: unknown; code?: unknown; detail?: unknown })
      : undefined;
  const message =
    (typeof problem?.detail === "string" && problem.detail) ||
    (typeof problem?.title === "string" && problem.title) ||
    `Запрос завершился с кодом ${response.status}.`;
  throw new AssessmentApiError(
    message,
    response.status,
    typeof problem?.code === "string" ? problem.code : undefined,
  );
}

/** Fetch and validate the API catalog through the generated client. */
export async function fetchAssessmentCatalog(): Promise<Schemas["ScaleDefinitionOut"][]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/assessments/scales");
  return requireData(data?.items, response, error);
}

/** Fetch one full definition for the form or its selected version. */
export async function fetchAssessmentDefinition(
  scaleId: string,
): Promise<Schemas["ScaleDefinitionOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/assessments/scales/{scale_id}", {
    params: { path: { scale_id: scaleId } },
  });
  return requireData(data, response, error);
}

/** Load one server history page. Results are only kept in the query cache. */
export async function fetchAssessmentHistory(input: {
  scaleId?: string;
  before?: string;
}): Promise<Schemas["AssessmentHistoryOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/assessments", {
    params: {
      query: {
        ...(input.scaleId ? { scale_id: input.scaleId } : {}),
        limit: 20,
        ...(input.before ? { before: input.before } : {}),
      },
    },
  });
  return requireData(data, response, error);
}
