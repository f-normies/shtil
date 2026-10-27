import { fetchClient, type Schemas } from "@compass/api-client";
import { latestDoctorMessage } from "./latest-doctor-message";
import type { DiaryScheduleSnapshot } from "./types";

export class DiaryApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "DiaryApiError";
  }
}

function requireData<T>(data: T | undefined, response: Response, error: unknown): T {
  if (data !== undefined) return data;
  const problem =
    error && typeof error === "object"
      ? (error as { title?: unknown; code?: unknown; detail?: unknown })
      : undefined;
  const message =
    (typeof problem?.detail === "string" && problem.detail) ||
    (typeof problem?.title === "string" && problem.title) ||
    `Запрос дневника завершился с кодом ${response.status}.`;
  throw new DiaryApiError(
    message,
    response.status,
    typeof problem?.code === "string" ? problem.code : undefined,
  );
}

/** Load the server's calendar context; callers cache only successful responses. */
export async function fetchDiaryContext(): Promise<Schemas["DiaryContextOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/diary/context");
  return requireData(data, response, error);
}

/** Load the actual #111 schedule and stamp its successful fetch time. */
export async function fetchDiarySchedule(): Promise<DiaryScheduleSnapshot> {
  const { data, error, response } = await fetchClient.GET("/api/v1/assessments/schedule");
  return { ...requireData(data, response, error), fetched_at: new Date().toISOString() };
}

/** Load real current prescriptions through the generated authenticated API client. */
export async function fetchDiaryPrescriptions(): Promise<Schemas["ScheduleItem"][]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/me/prescriptions");
  return requireData(data, response, error);
}

/** Load the doctor's latest message; the patient's own replies are skipped. */
export async function fetchLatestDiaryMessage(
  patientId: string,
): Promise<Schemas["MessageOut"] | null> {
  const { data, error, response } = await fetchClient.GET(
    "/api/v1/patients/{patient_id}/messages",
    { params: { path: { patient_id: patientId }, query: { limit: 20 } } },
  );
  const history = requireData(data, response, error);
  return latestDoctorMessage(history.items, patientId);
}

/** Load one server diary page through the generated API client. */
export async function fetchDiaryEntries(
  input: {
    start?: string;
    end?: string;
    cursor?: string;
    filter?: "all" | "mood" | "records";
    limit?: number;
  } = {},
): Promise<Schemas["DiaryEntryPage"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/diary/entries", {
    params: {
      query: {
        ...(input.start ? { start: input.start } : {}),
        ...(input.end ? { end: input.end } : {}),
        ...(input.cursor ? { cursor: input.cursor } : {}),
        ...(input.filter ? { filter: input.filter } : {}),
        ...(input.limit ? { limit: input.limit } : {}),
      },
    },
  });
  return requireData(data, response, error);
}

/** Refresh the current owner-visible version for one opened diary record. */
export async function fetchDiaryEntry(entryId: string): Promise<Schemas["DiaryEntryOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/diary/entries/{entry_id}", {
    params: { path: { entry_id: entryId } },
  });
  return requireData(data, response, error);
}

/** Ask the server to retry a terminal voice transcription through the existing endpoint. */
export async function retryDiaryTranscription(
  entryId: string,
  operationId: string,
): Promise<Schemas["DiaryTranscriptionRetryOut"]> {
  const { data, error, response } = await fetchClient.POST(
    "/api/v1/diary/entries/{entry_id}/transcription/retry",
    { params: { path: { entry_id: entryId } }, body: { operation_id: operationId } },
  );
  return requireData(data, response, error);
}

/** Load explicit patient-reported dose marks for one calendar day. */
export async function fetchIntakeMarks(scheduledOn: string): Promise<Schemas["IntakeMarksOut"]> {
  const { data, error, response } = await fetchClient.GET("/api/v1/diary/intake", {
    params: { query: { scheduled_on: scheduledOn } },
  });
  return requireData(data, response, error);
}

/** Create or idempotently replay one immutable diary command. */
export async function createDiaryEntry(
  payload: Schemas["DiaryEntryCreateIn"],
): Promise<Schemas["DiaryCommandReceipt"]> {
  const { data, error, response } = await fetchClient.POST("/api/v1/diary/entries", {
    body: payload,
  });
  return requireData(data, response, error);
}

/** Create or idempotently replay one immutable dose-mark command. */
export async function createIntakeMark(
  payload: Schemas["IntakeMarkCreateIn"],
): Promise<Schemas["IntakeMarkOut"]> {
  const { data, error, response } = await fetchClient.POST("/api/v1/diary/intake", {
    body: payload,
  });
  return requireData(data, response, error);
}
