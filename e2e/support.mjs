import { mkdir, rename, writeFile } from "node:fs/promises";
import { isIP } from "node:net";
import path from "node:path";

/**
 * Аргументы `uv` для запуска API в сквозном прогоне. Лимит кадра WebSocket тот же,
 * что у `make dev-api`: транспорт отклоняет кадры больше 4 КиБ до разбора JSON.
 */
export const API_SERVER_ARGS = Object.freeze([
  "run",
  "--directory",
  "apps/api",
  "uvicorn",
  "--factory",
  "e2e_test_app:create_test_app",
  "--no-proxy-headers",
  "--ws-max-size",
  "4096",
  "--host",
  "127.0.0.1",
  "--port",
  "8000",
]);

export function validateTestDatabaseUrl(value) {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error("COMPASS_DATABASE_URL must name the dedicated loopback database compass_e2e");
  }

  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("COMPASS_DATABASE_URL must be a valid PostgreSQL URL");
  }

  const host = url.hostname.replace(/^\[|\]$/g, "");
  const loopback =
    host === "localhost" ||
    (isIP(host) === 4 && Number(host.split(".")[0]) === 127) ||
    (isIP(host) === 6 && host === "::1");
  if (!loopback) throw new Error("COMPASS_DATABASE_URL must use a loopback database host");
  if (url.protocol !== "postgresql+asyncpg:") {
    throw new Error("COMPASS_DATABASE_URL must use postgresql+asyncpg");
  }
  if (url.search || url.hash) {
    throw new Error(
      "COMPASS_DATABASE_URL must not override the local database host in query options",
    );
  }
  const name = decodeURIComponent(url.pathname.slice(1));
  if (name !== "compass_e2e") {
    throw new Error("COMPASS_DATABASE_URL database name must be compass_e2e");
  }
  return value;
}

export function buildServiceEnvironment(parent, { databaseUrl, knowledgeDirectory }) {
  validateTestDatabaseUrl(databaseUrl);
  const inherited = Object.fromEntries(
    Object.entries(parent).filter(([name]) => {
      const upper = name.toUpperCase();
      return (
        !upper.startsWith("COMPASS_") &&
        !upper.startsWith("OPENAI_") &&
        !["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"].includes(upper)
      );
    }),
  );
  return {
    ...inherited,
    COMPASS_DATABASE_URL: databaseUrl,
    COMPASS_ENVIRONMENT: "test",
    COMPASS_LLM_URLS: "",
    COMPASS_EMBEDDINGS_URLS: "",
    COMPASS_ASR_URLS: "",
    COMPASS_KNOWLEDGE_DIR: knowledgeDirectory,
  };
}

/**
 * @typedef {{ scenario_id: string, coverage: { milestone: string, verified: string[], prepared_data: string[], not_covered: string[], full_scenario_passed: boolean }, status: "running" | "passed" | "failed", started_at: string, finished_at: string | null, durations_ms: { interaction_total: number | null, submission_to_alert: number | null }, actions: { doctor: { count: number, steps: string[] }, patient: { count: number, steps: string[] }, total: number }, tests: { expected: number, passed: number, failed: number }, failure: { stage: string | null, message: string | null } }} RunReport
 */

/** @returns {RunReport} */
export function createInitialReport() {
  return {
    scenario_id: "m1-assessment-realtime-alert",
    coverage: {
      milestone: "M1",
      verified: ["s3", "s8", "s9", "s10-alert-delivery-and-acknowledgment"],
      prepared_data: ["s2", "s7"],
      not_covered: ["s1", "s4", "s5", "s6", "s10-referral-confirmation", "s11"],
      full_scenario_passed: false,
    },
    status: "running",
    started_at: new Date().toISOString(),
    finished_at: null,
    durations_ms: {
      interaction_total: null,
      submission_to_alert: null,
    },
    actions: {
      doctor: { count: 0, steps: [] },
      patient: { count: 0, steps: [] },
      total: 0,
    },
    tests: { expected: 1, passed: 0, failed: 0 },
    failure: { stage: "input-validation", message: null },
  };
}

/**
 * @typedef {{ durations_ms: { interaction_total: number | null, submission_to_alert: number | null }, actions: { doctor: { count: number, steps: string[] }, patient: { count: number, steps: string[] } } }} E2EMetrics
 * @typedef {{ stage: string, message: string }} E2EFailure
 * @typedef {{ status: "passed" | "failed", passedTests?: number, failedTests?: number, metrics?: E2EMetrics | null, failure?: E2EFailure | null }} FinalizeOptions
 */

/** @param {RunReport} report @param {FinalizeOptions} options @returns {RunReport} */
export function finalizeReport(
  report,
  { status, passedTests = 0, failedTests = 0, metrics = null, failure = null },
) {
  const durations = metrics?.durations_ms ?? {};
  const actions = metrics?.actions;
  const actionCountsAreComplete = [actions?.doctor, actions?.patient].every(
    (role) =>
      Number.isInteger(role?.count) &&
      role.count >= 0 &&
      Array.isArray(role.steps) &&
      role.steps.length === role.count,
  );
  const completeMetrics =
    Number.isFinite(durations.interaction_total) &&
    Number.isFinite(durations.submission_to_alert) &&
    durations.interaction_total >= 0 &&
    durations.submission_to_alert >= 0 &&
    actionCountsAreComplete;
  const succeeded =
    status === "passed" &&
    passedTests === report.tests.expected &&
    failedTests === 0 &&
    Boolean(completeMetrics);
  const recordedActions = actions ?? report.actions;
  return {
    ...report,
    status: succeeded ? "passed" : "failed",
    finished_at: new Date().toISOString(),
    durations_ms: { ...report.durations_ms, ...durations },
    actions: {
      ...recordedActions,
      total: (recordedActions.doctor?.count ?? 0) + (recordedActions.patient?.count ?? 0),
    },
    tests: {
      expected: report.tests.expected,
      passed: passedTests,
      failed: failedTests,
    },
    failure: succeeded
      ? { stage: null, message: null }
      : {
          stage: failure?.stage ?? "playwright",
          message: failure?.message ?? "The run did not complete successfully",
        },
  };
}

export async function writeReport(filePath, report) {
  await mkdir(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  await rename(temporaryPath, filePath);
}
