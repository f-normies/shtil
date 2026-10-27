import type { Schemas } from "@compass/api-client";

export type PatientIdentity = Pick<Schemas["UserOut"], "id" | "role">;
export type OwnerPointer = { id: string; role: "patient"; locked: boolean };
export type DefinitionSnapshot = Schemas["ScaleDefinitionOut"];
export type AssessmentPayload = Schemas["AssessmentCreateIn"];

export type AssessmentDraft = {
  owner_id: string;
  scale_id: string;
  revision: number;
  answers: Record<string, number>;
  definition_snapshot: DefinitionSnapshot;
  updated_at: string;
};

export type OutboxState = "pending" | "blocked";
export type OutboxError = { status: number; code: string };
export type OutboxRecord = {
  owner_id: string;
  submission_id: string;
  payload: AssessmentPayload;
  definition_snapshot: DefinitionSnapshot;
  state: OutboxState;
  last_error: OutboxError | null;
  created_at: string;
};

export type SaveDraftInput = {
  ownerId: string;
  scaleId: string;
  expectedRevision: number;
  answers: Record<string, number>;
  definitionSnapshot: DefinitionSnapshot;
};

export type SaveDraftResult =
  | { status: "saved"; draft: AssessmentDraft }
  | { status: "conflict"; currentDraft: AssessmentDraft | null };

export type DiscardDraftResult =
  | { status: "discarded" }
  | { status: "conflict"; currentDraft: AssessmentDraft | null };

export type EnqueueDraftInput = {
  ownerId: string;
  scaleId: string;
  expectedRevision: number;
  payload: AssessmentPayload;
};

export type EnqueueDraftResult =
  | { status: "queued"; record: OutboxRecord }
  | { status: "conflict"; currentDraft: AssessmentDraft | null };

export type RestoreOutboxDraftResult =
  | { status: "restored"; draft: AssessmentDraft }
  | { status: "draft-exists"; currentDraft: AssessmentDraft }
  | { status: "unavailable" };
