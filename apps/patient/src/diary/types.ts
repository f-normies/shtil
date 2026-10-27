import type { Schemas } from "@compass/api-client";

export type DiaryQuickKind = "mood" | "text" | "voice";
export type DiaryDraftStep = "mood" | "story" | "intake" | "survey" | "complete";
export type DiarySurveyStatus = "unknown" | "pending" | "queued" | "completed" | "not_due";
export type IntakeView = {
  items: Schemas["IntakeMarkOut"][];
  source: "server" | "cache" | "empty" | "unavailable";
};

export type DiaryScheduleSnapshot = Schemas["ScheduleOut"] & { fetched_at: string };

export type DiaryContextCacheRecord = {
  owner_id: string;
  context: Schemas["DiaryContextOut"] | null;
  context_fetched_at: string | null;
  schedule_snapshot: DiaryScheduleSnapshot | null;
  prescriptions_snapshot?: Schemas["ScheduleItem"][] | null;
  prescriptions_fetched_at?: string | null;
  latest_message?: Schemas["MessageOut"] | null;
  latest_message_fetched_at?: string | null;
};

type DiaryDraftContent = {
  entryId?: string;
  operationId?: string;
  occurredAt: string;
  step: DiaryDraftStep;
  moodLevel: number | null;
  moodWords: string[];
  text: string;
  otherMedicineText?: string | null;
  scheduleSnapshot?: DiaryScheduleSnapshot | null;
  scheduleNeedsRefresh?: boolean;
  surveySubmissionIds?: string[];
};

export type QuickDiaryDraftInput = DiaryDraftContent & {
  ownerId: string;
  draftId: string;
  expectedRevision: number;
  mode: "quick";
  entryKind: DiaryQuickKind;
  surveyStatus?: "unknown";
  dailyOn?: never;
};

export type DailyDiaryDraftInput = DiaryDraftContent & {
  ownerId: string;
  draftId: string;
  expectedRevision: number;
  mode: "daily";
  entryKind: "daily";
  dailyOn: string;
  moodLevel: number;
  surveyStatus: DiarySurveyStatus;
};

export type DiaryDraftInput = QuickDiaryDraftInput | DailyDiaryDraftInput;

export type DiaryDraftRecord = {
  owner_id: string;
  draft_id: string;
  entry_id: string;
  operation_id: string;
  mode: "daily" | "quick";
  entry_kind: "daily" | DiaryQuickKind;
  occurred_at: string;
  daily_on?: string;
  step: DiaryDraftStep;
  mood_level: number | null;
  mood_words: string[];
  text: string;
  other_medicine_text: string | null;
  schedule_snapshot: DiaryScheduleSnapshot | null;
  schedule_needs_refresh: boolean;
  survey_status: DiarySurveyStatus;
  survey_submission_ids: string[];
  revision: number;
  updated_at: string;
};

export type DiaryOutboxState = "pending" | "blocked" | "conflict";
export type DiaryOutboxAction = "create" | "update" | "hide" | "voice";
export type DiaryCreateCommand = Schemas["DiaryEntryCreateIn"];
export type DiaryUpdateCommand = Schemas["DiaryEntryPatchIn"];
export type DiaryHideCommand = Schemas["DiaryHideIn"];
export type DiaryVoiceCommand = Schemas["DiaryVoiceUploadMetadataIn"];

export type DiaryEntryRecord = Pick<
  Schemas["DiaryEntryOut"],
  | "entry_id"
  | "kind"
  | "occurred_at"
  | "daily_on"
  | "mood_level"
  | "mood_words"
  | "text"
  | "other_medicine_text"
  | "audio"
  | "revision"
> & {
  owner_id: string;
  operation_id?: string;
  sync_state: "pending" | "blocked" | "conflict" | "synced";
  remote_snapshot?: Schemas["DiaryEntryOut"] | null;
  received_at?: string | null;
  source_statuses?: Schemas["DiaryEntryOut"]["source_statuses"];
  survey_status?: DiarySurveyStatus;
  survey_submission_ids?: string[];
  schedule_snapshot?: DiaryScheduleSnapshot | null;
  hidden?: boolean;
  server_revision?: number;
};

type DiaryOutboxBase = {
  owner_id: string;
  operation_id: string;
  entry_id: string;
  state: DiaryOutboxState;
  last_error: { status: number; code: string } | null;
  payload_conflicts: DiaryOutboxPayload[];
  conflicting_audio_sha256?: string;
  created_at: string;
};

export type DiaryOutboxPayload =
  | DiaryCreateCommand
  | DiaryUpdateCommand
  | DiaryHideCommand
  | DiaryVoiceCommand;

export type DiaryOutboxRecord =
  | (DiaryOutboxBase & { action: "create"; payload: DiaryCreateCommand })
  | (DiaryOutboxBase & { action: "update"; payload: DiaryUpdateCommand })
  | (DiaryOutboxBase & { action: "hide"; payload: DiaryHideCommand })
  | (DiaryOutboxBase & {
      action: "voice";
      payload: DiaryVoiceCommand;
      audio_sha256: string;
    });

export type IntakeCommand = Schemas["IntakeMarkCreateIn"];
export type IntakeOutboxState = "pending" | "blocked" | "conflict";
export type IntakeOutboxRecord = {
  owner_id: string;
  operation_id: string;
  payload: IntakeCommand;
  state: IntakeOutboxState;
  last_error: { status: number; code: string } | null;
  payload_conflicts: IntakeCommand[];
  created_at: string;
};
export type IntakeCacheRecord = {
  owner_id: string;
  prescription_id: string;
  scheduled_on: string;
  dose_ordinal: number;
  mark: Schemas["IntakeMarkOut"];
  fetched_at: string;
};

export type DiaryAudioRecord = {
  owner_id: string;
  entry_id: string;
  blob: Blob;
  sha256: string;
  saved_at: string;
};

export type EnqueueDiaryEntryResult =
  | { status: "queued" | "replayed"; entry: DiaryEntryRecord; command: DiaryOutboxRecord }
  | { status: "daily-exists"; currentEntry: DiaryEntryRecord }
  | { status: "conflict"; command: DiaryOutboxRecord };

export type EnqueueDiaryCommandResult =
  | { status: "queued" | "replayed"; entry: DiaryEntryRecord; command: DiaryOutboxRecord }
  | { status: "conflict"; command: DiaryOutboxRecord };

export type EnqueueIntakeMarkResult =
  | { status: "queued" | "replayed"; command: IntakeOutboxRecord }
  | { status: "conflict"; command: IntakeOutboxRecord };

export type SaveDiaryDraftResult =
  | { status: "saved"; draft: DiaryDraftRecord }
  | { status: "conflict"; currentDraft: DiaryDraftRecord | null }
  | { status: "daily-exists"; currentDraft: DiaryDraftRecord };

type FinalizeDailyCheckInBase = {
  ownerId: string;
  draftId: string;
  expectedDraftRevision: number;
  surveyStatus: DiarySurveyStatus;
  surveySubmissionIds: string[];
  assessmentDrafts: import("../assessments/types").EnqueueDraftInput[];
};

export type FinalizeDailyCheckInInput = FinalizeDailyCheckInBase &
  (
    | { payload: DiaryCreateCommand; voiceBlob?: never }
    | { payload: DiaryVoiceCommand; voiceBlob: Blob }
  );

export type FinalizeDailyCheckInResult =
  | EnqueueDiaryEntryResult
  | { status: "draft-conflict"; currentDraft: DiaryDraftRecord | null };

export type FinalizeQuickTextInput = {
  ownerId: string;
  draftId: string;
  expectedDraftRevision: number;
  payload: DiaryCreateCommand;
};

export type FinalizeQuickTextResult =
  | EnqueueDiaryEntryResult
  | { status: "draft-conflict"; currentDraft: DiaryDraftRecord | null };
