import type { Schemas } from "@compass/api-client";
import { fetchClient, fetchCurrentUser, getSessionGeneration } from "@compass/api-client";
import type { PatientIdentity } from "../assessments/types";

export type DiaryCommandResponse = {
  status: number | null;
  code?: string;
  receipt?: Schemas["DiaryCommandReceipt"];
};
export type IntakeCommandResponse = {
  status: number | null;
  code?: string;
  mark?: Schemas["IntakeMarkOut"];
};
export type DiaryEntryFetchResponse =
  | Schemas["DiaryEntryOut"]
  | null
  | { status: number | null; code?: string; entry?: Schemas["DiaryEntryOut"] };

export type DiarySyncAdapter = {
  getCurrentUser: () => Promise<PatientIdentity | null>;
  getSessionGeneration?: () => number;
  createEntry: (payload: Schemas["DiaryEntryCreateIn"]) => Promise<DiaryCommandResponse>;
  updateEntry?: (
    entryId: string,
    payload: Schemas["DiaryEntryPatchIn"],
  ) => Promise<DiaryCommandResponse>;
  hideEntry?: (entryId: string, payload: Schemas["DiaryHideIn"]) => Promise<DiaryCommandResponse>;
  uploadVoice?: (
    payload: Schemas["DiaryVoiceUploadMetadataIn"],
    blob: Blob,
  ) => Promise<DiaryCommandResponse>;
  createIntakeMark?: (payload: Schemas["IntakeMarkCreateIn"]) => Promise<IntakeCommandResponse>;
  fetchEntry?: (entryId: string) => Promise<DiaryEntryFetchResponse>;
};

function problemCode(error: unknown): string | undefined {
  if (error && typeof error === "object" && "code" in error && typeof error.code === "string") {
    return error.code;
  }
  return undefined;
}

/** Serialize the server's metadata JSON field and the original recording Blob. */
export function createDiaryVoiceUploadFormData(
  metadata: Schemas["DiaryVoiceUploadMetadataIn"],
  blob: Blob,
): FormData {
  const form = new FormData();
  const extension = blob.type.toLowerCase().startsWith("audio/mp4") ? "mp4" : "webm";
  form.append("metadata", JSON.stringify(metadata));
  form.append("file", blob, `diary-audio.${extension}`);
  return form;
}

/** Use the generated client for every authenticated diary write and conflict read. */
export const diarySyncAdapter: DiarySyncAdapter = {
  getCurrentUser: () => fetchCurrentUser(fetchClient),
  getSessionGeneration: () => getSessionGeneration(fetchClient),
  async createEntry(payload) {
    const { data, error, response } = await fetchClient.POST("/api/v1/diary/entries", {
      body: payload,
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { receipt: data } : {}),
    };
  },
  async updateEntry(entryId, payload) {
    const { data, error, response } = await fetchClient.PATCH("/api/v1/diary/entries/{entry_id}", {
      params: { path: { entry_id: entryId } },
      body: payload,
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { receipt: data } : {}),
    };
  },
  async hideEntry(entryId, payload) {
    const { data, error, response } = await fetchClient.POST(
      "/api/v1/diary/entries/{entry_id}/hide",
      { params: { path: { entry_id: entryId } }, body: payload },
    );
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { receipt: data } : {}),
    };
  },
  async uploadVoice(payload, blob) {
    const form = createDiaryVoiceUploadFormData(payload, blob);
    const { data, error, response } = await fetchClient.POST("/api/v1/diary/entries/voice", {
      body: { metadata: payload, file: "" },
      bodySerializer: () => form,
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { receipt: data } : {}),
    };
  },
  async createIntakeMark(payload) {
    const { data, error, response } = await fetchClient.POST("/api/v1/diary/intake", {
      body: payload,
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { mark: data } : {}),
    };
  },
  async fetchEntry(entryId) {
    const { data, error, response } = await fetchClient.GET("/api/v1/diary/entries/{entry_id}", {
      params: { path: { entry_id: entryId } },
    });
    const code = problemCode(error);
    return {
      status: response.status,
      ...(code ? { code } : {}),
      ...(data ? { entry: data } : {}),
    };
  },
};

export function fetchedEntry(value: DiaryEntryFetchResponse): {
  status: number | null;
  code?: string;
  entry?: Schemas["DiaryEntryOut"];
} {
  if (value && "status" in value) return value;
  return value ? { status: 200, entry: value } : { status: 404 };
}
