import type { Schemas } from "@compass/api-client";
import type { BrowserContext, Page, Route } from "@playwright/test";
import { DEFINITION_FORM, PATIENT_A, PATIENT_B } from "./assessment-fixtures";

export const SYNTHETIC_USERS = {
  a: {
    id: PATIENT_A,
    role: "patient",
    full_name: "Синтетический пациент А",
    email: "patient-a@example.invalid",
  },
  b: {
    id: PATIENT_B,
    role: "patient",
    full_name: "Синтетический пациент Б",
    email: "patient-b@example.invalid",
  },
} satisfies Record<string, Schemas["UserOut"]>;

type AssessmentApiOptions = {
  definitions?: Schemas["ScaleDefinitionOut"][];
  history?: Schemas["AssessmentOut"][];
  user?: Schemas["UserOut"] | null;
  // Экран помощи (#57): без данных — 404, как у неизвестного маршрута.
  patientHelp?: Schemas["PatientHelpOut"];
  alertRaised?: (payload: Schemas["AssessmentCreateIn"]) => boolean;
};

export type SubmissionResponseOverride = { status: number; code?: string };
export type HistoryRequest = {
  userId: string | null;
  scaleId: string | null;
  before: string | null;
};
export type AssessmentApiMock = {
  submissions: Schemas["AssessmentCreateIn"][];
  acceptedSubmissions: Schemas["AssessmentCreateIn"][];
  acknowledgedSubmissions: Schemas["AssessmentCreateIn"][];
  historyRequests: HistoryRequest[];
  setAuthenticatedUser: (user: Schemas["UserOut"] | null) => void;
  setDefinitions: (definitions: Schemas["ScaleDefinitionOut"][]) => void;
  setHistory: (history: Schemas["AssessmentOut"][]) => void;
  setNetworkAvailable: (available: boolean) => void;
  setNextSubmissionResponse: (status: number, code: string) => void;
  setSubmissionResponseSequence: (responses: SubmissionResponseOverride[]) => void;
  setNextLogoutStatus: (status: number) => void;
  setCatalogFailure: (status: number) => void;
  holdNextSessionResponse: (page: Page) => void;
  releaseHeldSessionResponse: () => void;
  heldSessionResponses: () => number;
  holdNextSubmissionResponse: () => void;
  releaseHeldSubmissionResponse: () => void;
  heldSubmissionResponses: () => number;
  loseNextSubmissionResponse: () => void;
};

function json(route: Route, status: number, body: unknown): Promise<void> {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function mockAssessmentApi(
  context: BrowserContext,
  options: AssessmentApiOptions = {},
): Promise<AssessmentApiMock> {
  let currentUser = options.user === undefined ? SYNTHETIC_USERS.a : options.user;
  let definitions = options.definitions ?? [DEFINITION_FORM];
  let history = options.history ?? [];
  let networkAvailable = true;
  let submissionResponseSequence: SubmissionResponseOverride[] = [];
  let nextLogoutStatus: number | null = null;
  let catalogFailureStatus: number | null = null;
  let holdNextSession = false;
  let heldSessionPage: Page | null = null;
  let releaseHeldSession: (() => void) | null = null;
  let heldSessionResponseCount = 0;
  let holdNextSubmission = false;
  let releaseHeldSubmission: (() => void) | null = null;
  let heldSubmissionResponseCount = 0;
  let loseNextResponse = false;
  const submissions: Schemas["AssessmentCreateIn"][] = [];
  const acceptedSubmissions: Schemas["AssessmentCreateIn"][] = [];
  const acknowledgedSubmissions: Schemas["AssessmentCreateIn"][] = [];
  const historyRequests: HistoryRequest[] = [];
  const acceptedBySubmissionId = new Map<string, Schemas["AssessmentCreatedOut"]>();

  await context.route("**/api/**", async (route) => {
    if (!networkAvailable) {
      await route.abort("failed");
      return;
    }

    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/auth/me" && request.method() === "GET") {
      const responseUser = currentUser;
      const requestPage = request.frame().page();
      if (holdNextSession && requestPage === heldSessionPage) {
        holdNextSession = false;
        heldSessionPage = null;
        heldSessionResponseCount += 1;
        await new Promise<void>((resolve) => {
          releaseHeldSession = resolve;
        });
        releaseHeldSession = null;
      }
      if (!responseUser) await json(route, 401, { status: 401, title: "Не выполнен вход" });
      else await json(route, 200, responseUser);
      return;
    }
    if (url.pathname === "/api/v1/auth/login" && request.method() === "POST") {
      const body = request.postDataJSON() as { email?: string };
      currentUser = body.email === SYNTHETIC_USERS.b.email ? SYNTHETIC_USERS.b : SYNTHETIC_USERS.a;
      await json(route, 200, currentUser);
      return;
    }
    if (url.pathname === "/api/v1/me/preferences") {
      await json(route, 200, {
        display_name: "Синтетический пациент",
        reminder_time: "21:00",
        mood_palette: "warm",
        story_mode: "voice",
        text_size: "normal",
        onboarding_completed_at: "2026-10-25T09:00:00Z",
        revision: 0,
        timezone: "Europe/Moscow",
        updated_at: null,
      });
      return;
    }
    if (url.pathname === "/api/v1/auth/logout" && request.method() === "POST") {
      if (nextLogoutStatus !== null) {
        const status = nextLogoutStatus;
        nextLogoutStatus = null;
        if (status === 401) currentUser = null;
        await json(route, status, { status, title: "Synthetic logout rejection" });
        return;
      }
      currentUser = null;
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (url.pathname === "/api/v1/assessments/scales" && request.method() === "GET") {
      if (catalogFailureStatus !== null) {
        await json(route, catalogFailureStatus, {
          status: catalogFailureStatus,
          title: "Synthetic catalog failure",
        });
        return;
      }
      await json(route, 200, { items: definitions });
      return;
    }
    if (url.pathname.startsWith("/api/v1/assessments/scales/") && request.method() === "GET") {
      const id = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
      const definition = definitions.find((candidate) => candidate.scale.id === id);
      if (definition) await json(route, 200, definition);
      else await json(route, 404, { status: 404, title: "Опросник не найден" });
      return;
    }
    if (url.pathname === "/api/v1/assessments" && request.method() === "GET") {
      if (!currentUser) {
        await json(route, 401, { status: 401, title: "Не выполнен вход" });
        return;
      }
      const authenticatedUser = currentUser;
      const requestedScaleId = url.searchParams.get("scale_id");
      const matching = history
        .filter(
          (item) =>
            item.patient_id === authenticatedUser.id &&
            (!requestedScaleId || item.scale_id === requestedScaleId),
        )
        .sort((left, right) => right.id.localeCompare(left.id));
      const olderPage = url.searchParams.has("before");
      historyRequests.push({
        userId: authenticatedUser.id,
        scaleId: requestedScaleId,
        before: url.searchParams.get("before"),
      });
      await json(route, 200, {
        items: olderPage ? matching.slice(1) : matching.slice(0, 1),
        next_cursor: !olderPage && matching.length > 1 ? "synthetic-older-page" : null,
      });
      return;
    }
    if (
      url.pathname === "/api/v1/patient-help" &&
      request.method() === "GET" &&
      options.patientHelp
    ) {
      await json(route, 200, options.patientHelp);
      return;
    }
    if (url.pathname === "/api/v1/assessments" && request.method() === "POST") {
      const payload = request.postDataJSON() as Schemas["AssessmentCreateIn"];
      if (!currentUser) {
        await json(route, 401, { status: 401, title: "Не выполнен вход" });
        return;
      }
      if (currentUser.role !== "patient" || payload.patient_id !== currentUser.id) {
        await json(route, 403, {
          status: 403,
          code: "patient_mismatch",
          title: "Assessment belongs to another patient",
        });
        return;
      }
      submissions.push(payload);
      const accepted = acceptedBySubmissionId.get(payload.submission_id);
      if (accepted) {
        await json(route, 201, accepted);
        acknowledgedSubmissions.push(payload);
        return;
      }
      const response = submissionResponseSequence.shift();
      if (response && response.status !== 201) {
        await json(route, response.status, {
          status: response.status,
          ...(response.code ? { code: response.code } : {}),
          title: "Synthetic assessment rejection",
        });
        return;
      }
      const definition = definitions.find((candidate) => candidate.scale.id === payload.scale_id);
      if (!definition) {
        await json(route, 404, {
          status: 404,
          code: "scale_unavailable",
          title: "Synthetic scale unavailable",
        });
        return;
      }
      const result: Schemas["AssessmentCreatedOut"] = {
        alert_raised: options.alertRaised?.(payload) ?? false,
        id: "00000000-0000-4000-8000-000000000021",
        patient_id: payload.patient_id,
        submission_id: payload.submission_id,
        scale_id: payload.scale_id,
        scale_title: definition?.scale.title ?? "Synthetic wellbeing check",
        scale_version: payload.scale_version,
        completed_at: payload.completed_at,
        received_at: "2026-10-17T12:00:00.000Z",
        answers: payload.answers,
        scores: {
          calmness: {
            value: payload.answers.calm ?? 0,
            band: {
              code: "synthetic",
              label: "Synthetic calmness band",
              source: {
                id: "synthetic-validation",
                title: "Synthetic validation source",
                url: "https://example.invalid/validation",
                locator: "section 1",
                supplementary: true,
              },
            },
          },
          rest: {
            value: payload.answers.rest ?? 0,
            band: {
              code: "synthetic",
              label: "Synthetic rest band",
              source: {
                id: "synthetic-validation",
                title: "Synthetic validation source",
                url: "https://example.invalid/validation",
                locator: "section 1",
                supplementary: true,
              },
            },
          },
        },
        sources: [
          {
            id: "synthetic-validation",
            title: "Synthetic validation source",
            url: "https://example.invalid/validation",
            supplementary: true,
          },
        ],
      };
      acceptedBySubmissionId.set(payload.submission_id, result);
      acceptedSubmissions.push(payload);
      history = [result, ...history];
      if (loseNextResponse) {
        loseNextResponse = false;
        await route.abort("failed");
        return;
      }
      if (holdNextSubmission) {
        holdNextSubmission = false;
        heldSubmissionResponseCount += 1;
        await new Promise<void>((resolve) => {
          releaseHeldSubmission = resolve;
        });
        releaseHeldSubmission = null;
      }
      await json(route, 201, result);
      acknowledgedSubmissions.push(payload);
      return;
    }
    await json(route, 404, { status: 404, title: "Неизвестный синтетический маршрут" });
  });

  return {
    submissions,
    acceptedSubmissions,
    acknowledgedSubmissions,
    historyRequests,
    setAuthenticatedUser: (user) => {
      currentUser = user;
    },
    setDefinitions: (value) => {
      definitions = value;
    },
    setHistory: (value) => {
      history = value;
    },
    setNetworkAvailable: (value) => {
      networkAvailable = value;
    },
    setNextSubmissionResponse: (status, code) => {
      submissionResponseSequence = [{ status, code }];
    },
    setSubmissionResponseSequence: (responses) => {
      submissionResponseSequence = [...responses];
    },
    setNextLogoutStatus: (status) => {
      nextLogoutStatus = status;
    },
    setCatalogFailure: (status) => {
      catalogFailureStatus = status;
    },
    holdNextSessionResponse: (page) => {
      holdNextSession = true;
      heldSessionPage = page;
    },
    releaseHeldSessionResponse: () => {
      releaseHeldSession?.();
    },
    heldSessionResponses: () => heldSessionResponseCount,
    holdNextSubmissionResponse: () => {
      holdNextSubmission = true;
    },
    releaseHeldSubmissionResponse: () => {
      releaseHeldSubmission?.();
    },
    heldSubmissionResponses: () => heldSubmissionResponseCount,
    loseNextSubmissionResponse: () => {
      loseNextResponse = true;
    },
  };
}
