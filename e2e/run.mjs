import { spawn } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ProcessLifecycle } from "./lifecycle.mjs";
import {
  API_SERVER_ARGS,
  buildServiceEnvironment,
  createInitialReport,
  finalizeReport,
  validateTestDatabaseUrl,
  writeReport,
} from "./support.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const E2E_DIR = path.dirname(fileURLToPath(import.meta.url));
const SERVICE_TIMEOUT_MS = 60_000;
const SYNTHETIC_PASSWORD = "Compass-E2E-21";

function runId() {
  return `${new Date().toISOString().replace(/[.:]/g, "-")}-${process.pid}`;
}

function sanitizeLog(text) {
  return text
    .replace(/(postgres(?:ql)?(?:\+asyncpg)?:\/\/)[^@/\s]*@/gi, "$1[redacted]@")
    .replaceAll(SYNTHETIC_PASSWORD, "[redacted]");
}

function appendHeader(stream, command, args, cwd) {
  stream.write(`$ ${command} ${args.join(" ")}\nworkdir: ${cwd}\n\n`);
}

async function runCommand(name, command, args, { cwd, env, logPath, lifecycle }) {
  lifecycle.throwIfInterrupted();
  const log = createWriteStream(logPath, { flags: "a" });
  appendHeader(log, command, args, cwd);
  const child = spawn(command, args, {
    cwd,
    env,
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  lifecycle.track(child);
  child.stdout.on("data", (chunk) => {
    const text = sanitizeLog(String(chunk));
    log.write(text);
    if (name === "playwright") process.stdout.write(text);
  });
  child.stderr.on("data", (chunk) => {
    const text = sanitizeLog(String(chunk));
    log.write(text);
    if (name === "playwright") process.stderr.write(text);
  });

  const result = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  await new Promise((resolve) => log.end(resolve));
  if (result.code !== 0) {
    const suffix = result.signal ? `signal ${result.signal}` : `exit code ${result.code}`;
    throw new Error(`${name} failed with ${suffix}; see ${path.relative(ROOT, logPath)}`);
  }
}

async function assertPortFree(port) {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => server.close(resolve));
  }).catch((error) => {
    if (error.code === "EADDRINUSE") {
      throw new Error(`port ${port} is already occupied; stop its process before E2E`);
    }
    throw error;
  });
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForReady(service, url, timeoutMs = SERVICE_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (service.spawnError) throw service.spawnError;
    if (service.child.exitCode !== null || service.child.signalCode !== null) {
      throw new Error(
        `${service.name} exited before becoming ready; see ${service.logRelativePath}`,
      );
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_500) });
      if (response.ok) return;
    } catch {
      // The service is still starting. Its output is saved in the run artifact directory.
    }
    await wait(250);
  }
  throw new Error(`${service.name} did not become ready within ${timeoutMs} ms`);
}

async function startService(name, command, args, { cwd, env, logPath, readyUrl, lifecycle }) {
  lifecycle.throwIfInterrupted();
  const log = createWriteStream(logPath, { flags: "a" });
  appendHeader(log, command, args, cwd);
  const child = spawn(command, args, {
    cwd,
    env,
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  lifecycle.track(child);
  child.stdout.on("data", (chunk) => log.write(sanitizeLog(String(chunk))));
  child.stderr.on("data", (chunk) => log.write(sanitizeLog(String(chunk))));
  const service = {
    name,
    child,
    log,
    logRelativePath: path.relative(ROOT, logPath),
    spawnError: null,
  };
  child.once("error", (error) => {
    service.spawnError = error;
  });
  try {
    await waitForReady(service, readyUrl);
  } catch (error) {
    await stopService(service, lifecycle);
    throw error;
  }
  console.log(`${name}: ready at ${readyUrl}`);
  return service;
}

async function stopService(service, lifecycle) {
  try {
    await lifecycle.stop(service.child);
  } finally {
    await new Promise((resolve) => service.log.end(resolve));
  }
}

async function main() {
  const lifecycle = new ProcessLifecycle();
  lifecycle.install();
  const outputDirectory = path.join(E2E_DIR, "artifacts", runId());
  const logsDirectory = path.join(outputDirectory, "logs");
  const reportPath = path.join(outputDirectory, "report.json");
  let report = createInitialReport();
  let stage = "report-initialization";
  const services = [];
  try {
    await mkdir(logsDirectory, { recursive: true });
    await writeReport(reportPath, report);
    lifecycle.throwIfInterrupted();

    stage = "input-validation";
    if (Number(process.versions.node.split(".")[0]) < 24) {
      throw new Error("E2E requires Node.js 24 or newer");
    }
    const databaseUrl = validateTestDatabaseUrl(process.env.COMPASS_DATABASE_URL);
    const serviceEnvironment = buildServiceEnvironment(process.env, {
      databaseUrl,
      knowledgeDirectory: path.join(ROOT, "knowledge"),
    });

    stage = "database-reset";
    await runCommand(
      "E2E database reset",
      "uv",
      ["run", "--directory", "apps/api", "alembic", "downgrade", "base"],
      {
        cwd: ROOT,
        env: serviceEnvironment,
        logPath: path.join(logsDirectory, "database-reset.log"),
        lifecycle,
      },
    );

    stage = "database-migrations";
    await runCommand(
      "database migrations",
      "uv",
      ["run", "--directory", "apps/api", "alembic", "upgrade", "head"],
      {
        cwd: ROOT,
        env: serviceEnvironment,
        logPath: path.join(logsDirectory, "migrations.log"),
        lifecycle,
      },
    );

    stage = "seed";
    await runCommand(
      "synthetic seed",
      "uv",
      ["run", "--directory", "apps/api", "python", "-m", "clin_compass.cli", "seed"],
      {
        cwd: ROOT,
        env: { ...serviceEnvironment, COMPASS_SEED_PASSWORD: SYNTHETIC_PASSWORD },
        logPath: path.join(logsDirectory, "seed.log"),
        lifecycle,
      },
    );

    stage = "port-check";
    for (const port of [8000, 5173, 5174]) await assertPortFree(port);

    stage = "api-startup";
    services.push(
      await startService("API", "uv", [...API_SERVER_ARGS], {
        cwd: ROOT,
        env: serviceEnvironment,
        logPath: path.join(logsDirectory, "api.log"),
        readyUrl: "http://127.0.0.1:8000/api/v1/health",
        lifecycle,
      }),
    );

    stage = "doctor-startup";
    services.push(
      await startService(
        "doctor Vite",
        "pnpm",
        [
          "--dir",
          "apps/doctor",
          "exec",
          "vite",
          "--host",
          "127.0.0.1",
          "--port",
          "5173",
          "--strictPort",
        ],
        {
          cwd: ROOT,
          env: serviceEnvironment,
          logPath: path.join(logsDirectory, "doctor-vite.log"),
          readyUrl: "http://127.0.0.1:5173/doctor/",
          lifecycle,
        },
      ),
    );

    stage = "patient-startup";
    services.push(
      await startService(
        "patient Vite",
        "pnpm",
        [
          "--dir",
          "apps/patient",
          "exec",
          "vite",
          "--host",
          "127.0.0.1",
          "--port",
          "5174",
          "--strictPort",
        ],
        {
          cwd: ROOT,
          env: serviceEnvironment,
          logPath: path.join(logsDirectory, "patient-vite.log"),
          readyUrl: "http://127.0.0.1:5174/patient/",
          lifecycle,
        },
      ),
    );

    stage = "playwright";
    const testEnvironment = {
      ...serviceEnvironment,
      E2E_REPORT_PATH: reportPath,
      E2E_RESULTS_DIR: path.join(outputDirectory, "playwright-results"),
      E2E_HTML_REPORT_DIR: path.join(outputDirectory, "html-report"),
    };
    await runCommand(
      "playwright",
      "pnpm",
      ["exec", "playwright", "test", "--config", "e2e/playwright.config.ts"],
      {
        cwd: ROOT,
        env: testEnvironment,
        logPath: path.join(logsDirectory, "playwright.log"),
        lifecycle,
      },
    );
    lifecycle.throwIfInterrupted();
    report = JSON.parse(await readFile(reportPath, "utf8"));
    if (report.status !== "passed") {
      throw new Error("Playwright finished without complete successful measurements");
    }
    console.log(`E2E report and artifacts: ${path.relative(ROOT, outputDirectory)}`);
  } catch (error) {
    const failure = {
      stage,
      message: lifecycle.interruptedBy
        ? `Interrupted by ${lifecycle.interruptedBy} during ${stage}`
        : error instanceof Error
          ? error.message
          : String(error),
    };
    try {
      const latestReport = JSON.parse(await readFile(reportPath, "utf8"));
      report =
        latestReport.status === "failed"
          ? latestReport
          : finalizeReport(latestReport, {
              status: "failed",
              passedTests: latestReport.tests?.passed ?? 0,
              failedTests: latestReport.tests?.failed ?? 0,
              failure,
            });
    } catch (reportError) {
      report = finalizeReport(report, {
        status: "failed",
        passedTests: report.tests?.passed ?? 0,
        failedTests: report.tests?.failed ?? 0,
        failure: {
          stage,
          message: `${failure.message}; report read failed: ${reportError instanceof Error ? reportError.message : String(reportError)}`,
        },
      });
    }
    await writeReport(reportPath, report);
    console.error(`E2E failed at ${stage}: ${report.failure.message}`);
    console.error(`E2E artifacts: ${path.relative(ROOT, outputDirectory)}`);
    process.exitCode = 1;
  } finally {
    try {
      await Promise.all(services.reverse().map((service) => stopService(service, lifecycle)));
    } finally {
      try {
        await lifecycle.stopAll();
      } finally {
        lifecycle.uninstall();
      }
    }
  }
}

await main();
