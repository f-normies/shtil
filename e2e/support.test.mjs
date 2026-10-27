import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { chmod, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  API_SERVER_ARGS,
  buildServiceEnvironment,
  createInitialReport,
  finalizeReport,
  validateTestDatabaseUrl,
  writeReport,
} from "./support.mjs";

const E2E_DIRECTORY = path.dirname(fileURLToPath(import.meta.url));
const REPOSITORY = path.resolve(E2E_DIRECTORY, "..");

async function waitFor(readValue, message, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await readValue();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(message);
}

function waitForExit(child, timeoutMs = 3_000) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve([child.exitCode, child.signalCode]);
  }
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeoutMs);
    child.once("exit", (code, signal) => {
      clearTimeout(timer);
      resolve([code, signal]);
    });
  });
}

function processIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error.code === "ESRCH") return false;
    throw error;
  }
}

function killProcessIfAlive(pid) {
  if (!pid || !processIsAlive(pid)) return;
  try {
    process.kill(pid, "SIGKILL");
  } catch (error) {
    if (error.code !== "ESRCH") throw error;
  }
}

async function runnerPortsAvailable() {
  for (const port of [8000, 5173, 5174]) {
    const server = net.createServer();
    const free = await new Promise((resolve, reject) => {
      server.once("error", (error) => {
        if (["EADDRINUSE", "EACCES", "EPERM"].includes(error.code)) resolve(false);
        else reject(error);
      });
      server.listen(port, "127.0.0.1", () => {
        server.close((error) => (error ? reject(error) : resolve(true)));
      });
    });
    if (!free) return false;
  }
  return true;
}

test("accepts only an explicit loopback E2E database", () => {
  assert.equal(
    validateTestDatabaseUrl("postgresql+asyncpg://compass:compass@127.0.0.1:55421/compass_e2e"),
    "postgresql+asyncpg://compass:compass@127.0.0.1:55421/compass_e2e",
  );
  assert.throws(
    () => validateTestDatabaseUrl("postgresql+asyncpg://user:secret@db.example/compass_e2e"),
    /loopback/,
  );
  assert.throws(
    () => validateTestDatabaseUrl("postgresql+asyncpg://user:secret@127.0.0.1/compass_dev"),
    /compass_e2e/,
  );
  assert.throws(
    () => validateTestDatabaseUrl("postgresql+asyncpg://user:secret@127.0.0.1/other_e2e"),
    /compass_e2e/,
  );
});

test("starts the API with the same 4 KiB WebSocket frame limit as make dev-api", () => {
  const flag = API_SERVER_ARGS.indexOf("--ws-max-size");
  assert.notEqual(flag, -1, "uvicorn must receive --ws-max-size");
  assert.equal(API_SERVER_ARGS[flag + 1], "4096");
});

test("builds a child environment without inherited Compass settings", () => {
  const env = buildServiceEnvironment(
    {
      PATH: "/bin",
      COMPASS_DATABASE_URL: "postgresql+asyncpg://old:old@127.0.0.1/compass_dev",
      COMPASS_LLM_URLS: "http://model.example:8000",
      COMPASS_ENVIRONMENT: "stand",
      HOME: "/tmp/user",
    },
    {
      databaseUrl: "postgresql+asyncpg://compass:compass@127.0.0.1/compass_e2e",
      knowledgeDirectory: "/tmp/e2e-knowledge",
    },
  );

  assert.equal(env.PATH, "/bin");
  assert.equal(env.HOME, "/tmp/user");
  assert.equal(
    env.COMPASS_DATABASE_URL,
    "postgresql+asyncpg://compass:compass@127.0.0.1/compass_e2e",
  );
  assert.equal(env.COMPASS_KNOWLEDGE_DIR, "/tmp/e2e-knowledge");
  assert.equal(env.COMPASS_ENVIRONMENT, "test");
  assert.equal(env.COMPASS_LLM_URLS, "");
  assert.equal(env.COMPASS_EMBEDDINGS_URLS, "");
  assert.equal(env.COMPASS_ASR_URLS, "");
});

test("writes a running report before tests and never marks an incomplete run successful", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cc-e2e-report-"));
  const reportPath = path.join(root, "report.json");
  try {
    const initial = createInitialReport();
    await writeReport(reportPath, initial);
    const onDiskBeforeTest = JSON.parse(await readFile(reportPath, "utf8"));
    assert.equal(onDiskBeforeTest.status, "running");
    assert.equal(onDiskBeforeTest.durations_ms.interaction_total, null);
    assert.equal(onDiskBeforeTest.durations_ms.submission_to_alert, null);

    const final = finalizeReport(initial, { status: "failed", failedTests: 1 });
    await writeReport(reportPath, final);
    const onDiskAfterFailure = JSON.parse(await readFile(reportPath, "utf8"));
    assert.equal(onDiskAfterFailure.status, "failed");
    assert.equal(onDiskAfterFailure.durations_ms.interaction_total, null);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("setup failures do not count as failed browser tests", () => {
  const report = finalizeReport(createInitialReport(), {
    status: "failed",
    failedTests: 0,
    failure: { stage: "database-reset", message: "reset failed" },
  });

  assert.equal(report.status, "failed");
  assert.deepEqual(report.tests, { expected: 1, passed: 0, failed: 0 });
  assert.equal(report.durations_ms.interaction_total, null);
  assert.equal(report.durations_ms.submission_to_alert, null);
});

test("reporter preserves zero completed tests when Playwright ends without test events", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cc-e2e-zero-tests-"));
  const reportPath = path.join(root, "report.json");
  const previousReportPath = process.env.E2E_REPORT_PATH;
  try {
    await writeReport(reportPath, createInitialReport());
    process.env.E2E_REPORT_PATH = reportPath;
    const { default: M1Reporter } = await import("./reporter.ts");
    const reporter = new M1Reporter();

    await reporter.onEnd({ status: "failed" });

    const saved = JSON.parse(await readFile(reportPath, "utf8"));
    assert.equal(saved.status, "failed");
    assert.deepEqual(saved.tests, { expected: 1, passed: 0, failed: 0 });
    assert.equal(saved.durations_ms.interaction_total, null);
    assert.equal(saved.durations_ms.submission_to_alert, null);
  } finally {
    if (previousReportPath === undefined) delete process.env.E2E_REPORT_PATH;
    else process.env.E2E_REPORT_PATH = previousReportPath;
    await rm(root, { recursive: true, force: true });
  }
});

test("reporter counts completed test events even when the report remains incomplete", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cc-e2e-test-counts-"));
  const reportPath = path.join(root, "report.json");
  const previousReportPath = process.env.E2E_REPORT_PATH;
  try {
    await writeReport(reportPath, createInitialReport());
    process.env.E2E_REPORT_PATH = reportPath;
    const { default: M1Reporter } = await import("./reporter.ts");
    const reporter = new M1Reporter();

    reporter.onTestEnd({}, { status: "passed", attachments: [] });
    await reporter.onEnd({ status: "passed" });

    const saved = JSON.parse(await readFile(reportPath, "utf8"));
    assert.equal(saved.status, "failed");
    assert.deepEqual(saved.tests, { expected: 1, passed: 1, failed: 0 });
  } finally {
    if (previousReportPath === undefined) delete process.env.E2E_REPORT_PATH;
    else process.env.E2E_REPORT_PATH = previousReportPath;
    await rm(root, { recursive: true, force: true });
  }
});

test("SIGTERM during database reset stops its child and finalizes an interrupted report", async (t) => {
  if (process.platform === "win32") {
    t.skip("the E2E runner uses POSIX process groups for detached children");
    return;
  }

  const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "cc-e2e-signal-"));
  const binDirectory = path.join(temporaryDirectory, "bin");
  const childPidPath = path.join(temporaryDirectory, "child.pid");
  const artifactsDirectory = path.join(E2E_DIRECTORY, "artifacts");
  let runner;
  let childPid;
  let runDirectory;

  try {
    await mkdir(binDirectory, { recursive: true });
    await mkdir(artifactsDirectory, { recursive: true });
    const stubUv = path.join(binDirectory, "uv");
    await writeFile(
      stubUv,
      `#!/bin/sh\nexec ${JSON.stringify(process.execPath)} -e 'require("node:fs").writeFileSync(process.env.E2E_STUB_CHILD_PID_FILE, String(process.pid)); setInterval(() => {}, 1000)'\n`,
    );
    await chmod(stubUv, 0o755);

    runner = spawn(process.execPath, [path.join(E2E_DIRECTORY, "run.mjs")], {
      cwd: REPOSITORY,
      env: {
        ...process.env,
        PATH: `${binDirectory}:${process.env.PATH}`,
        COMPASS_DATABASE_URL: "postgresql+asyncpg://compass:compass@127.0.0.1:55421/compass_e2e",
        E2E_STUB_CHILD_PID_FILE: childPidPath,
      },
      stdio: "ignore",
    });

    childPid = Number(
      await waitFor(async () => {
        try {
          return await readFile(childPidPath, "utf8");
        } catch (error) {
          if (error.code === "ENOENT") return null;
          throw error;
        }
      }, "the harmless database reset child did not start"),
    );
    runDirectory = await waitFor(async () => {
      const entries = await readdir(artifactsDirectory, { withFileTypes: true });
      const entry = entries.find(
        (item) => item.isDirectory() && item.name.endsWith(`-${runner.pid}`),
      );
      return entry ? path.join(artifactsDirectory, entry.name) : null;
    }, "the runner did not create its report directory");

    runner.kill("SIGTERM");
    const exit = await waitForExit(runner);
    assert.ok(exit, "the runner did not finish after SIGTERM");
    const [exitCode, exitSignal] = exit;
    assert.equal(processIsAlive(childPid), false, "the database reset child survived SIGTERM");
    assert.equal(exitSignal, null);
    assert.equal(exitCode, 1);

    const report = JSON.parse(await readFile(path.join(runDirectory, "report.json"), "utf8"));
    assert.equal(report.status, "failed");
    assert.equal(report.failure.stage, "database-reset");
    assert.equal(report.tests.expected, 1);
    assert.equal(report.tests.passed, 0);
    assert.equal(report.tests.failed, 0);
    assert.equal(report.durations_ms.interaction_total, null);
    assert.equal(report.durations_ms.submission_to_alert, null);
    assert.match(report.failure.message, /SIGTERM/);
  } finally {
    if (runner && runner.exitCode === null && runner.signalCode === null) {
      runner.kill("SIGKILL");
      await once(runner, "exit");
    }
    killProcessIfAlive(childPid);
    if (runDirectory) await rm(runDirectory, { recursive: true, force: true });
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});

test("SIGTERM while a service is starting stops it before readiness", async (t) => {
  if (process.platform === "win32") {
    t.skip("the E2E runner uses POSIX process groups for detached children");
    return;
  }
  if (!(await runnerPortsAvailable())) {
    t.skip("the E2E runner's local application ports are occupied");
    return;
  }

  const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "cc-e2e-service-signal-"));
  const binDirectory = path.join(temporaryDirectory, "bin");
  const childPidPath = path.join(temporaryDirectory, "child.pid");
  const artifactsDirectory = path.join(E2E_DIRECTORY, "artifacts");
  let runner;
  let childPid;
  let runDirectory;

  try {
    await mkdir(binDirectory, { recursive: true });
    await mkdir(artifactsDirectory, { recursive: true });
    const stubUv = path.join(binDirectory, "uv");
    await writeFile(
      stubUv,
      `#!/bin/sh\ncase "$*" in\n  *uvicorn*) exec ${JSON.stringify(process.execPath)} -e 'require("node:fs").writeFileSync(process.env.E2E_STUB_CHILD_PID_FILE, String(process.pid)); setInterval(() => {}, 1000)' ;;\nesac\nexit 0\n`,
    );
    await chmod(stubUv, 0o755);

    runner = spawn(process.execPath, [path.join(E2E_DIRECTORY, "run.mjs")], {
      cwd: REPOSITORY,
      env: {
        ...process.env,
        PATH: `${binDirectory}:${process.env.PATH}`,
        COMPASS_DATABASE_URL: "postgresql+asyncpg://compass:compass@127.0.0.1:55421/compass_e2e",
        E2E_STUB_CHILD_PID_FILE: childPidPath,
      },
      stdio: "ignore",
    });

    childPid = Number(
      await waitFor(async () => {
        try {
          return await readFile(childPidPath, "utf8");
        } catch (error) {
          if (error.code === "ENOENT") return null;
          throw error;
        }
      }, "the harmless service child did not start"),
    );
    runDirectory = await waitFor(async () => {
      const entries = await readdir(artifactsDirectory, { withFileTypes: true });
      const entry = entries.find(
        (item) => item.isDirectory() && item.name.endsWith(`-${runner.pid}`),
      );
      return entry ? path.join(artifactsDirectory, entry.name) : null;
    }, "the runner did not create its report directory");

    runner.kill("SIGTERM");
    const exit = await waitForExit(runner);
    assert.ok(exit, "the runner did not finish after SIGTERM");
    const [exitCode, exitSignal] = exit;
    assert.equal(processIsAlive(childPid), false, "the startup service survived SIGTERM");
    assert.equal(exitSignal, null);
    assert.equal(exitCode, 1);

    const report = JSON.parse(await readFile(path.join(runDirectory, "report.json"), "utf8"));
    assert.equal(report.status, "failed");
    assert.equal(report.failure.stage, "api-startup");
    assert.equal(report.tests.expected, 1);
    assert.equal(report.tests.passed, 0);
    assert.equal(report.tests.failed, 0);
    assert.equal(report.durations_ms.interaction_total, null);
    assert.equal(report.durations_ms.submission_to_alert, null);
    assert.match(report.failure.message, /SIGTERM/);
  } finally {
    if (runner && runner.exitCode === null && runner.signalCode === null) {
      runner.kill("SIGKILL");
      await once(runner, "exit");
    }
    killProcessIfAlive(childPid);
    if (runDirectory) await rm(runDirectory, { recursive: true, force: true });
    await rm(temporaryDirectory, { recursive: true, force: true });
  }
});

test("marks a completed run successful only when both role measurements are complete", () => {
  const report = createInitialReport();
  const metrics = {
    durations_ms: { interaction_total: 42_000, submission_to_alert: 800 },
    actions: {
      doctor: { count: 2, steps: ["Войти", "Подтвердить"] },
      patient: { count: 1, steps: ["Отправить ответы"] },
    },
  };

  const passed = finalizeReport(report, { status: "passed", passedTests: 1, metrics });
  assert.equal(passed.status, "passed");
  assert.equal(passed.durations_ms.interaction_total, 42_000);
  assert.equal(passed.actions.total, 3);

  const incomplete = finalizeReport(report, {
    status: "passed",
    passedTests: 1,
    metrics: { ...metrics, actions: { ...metrics.actions, doctor: { count: 3, steps: [] } } },
  });
  assert.equal(incomplete.status, "failed");
  assert.equal(incomplete.durations_ms.interaction_total, 42_000);
});
