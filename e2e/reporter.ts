import { readFileSync } from "node:fs";
import type { FullResult, Reporter, TestCase, TestResult } from "@playwright/test/reporter";
import { finalizeReport, writeReport } from "./support.mjs";

type Metrics = {
  durations_ms: {
    interaction_total: number | null;
    submission_to_alert: number | null;
  };
  actions: {
    doctor: { count: number; steps: string[] };
    patient: { count: number; steps: string[] };
  };
};

export default class M1Reporter implements Reporter {
  private metrics: Metrics | null = null;
  private passedTests = 0;
  private failedTests = 0;
  private failureMessage: string | null = null;

  onTestEnd(_test: TestCase, result: TestResult): void {
    if (result.status === "passed") {
      this.passedTests += 1;
    } else {
      this.failedTests += 1;
      this.failureMessage ??= result.error?.message ?? `Test ended with ${result.status}`;
    }
    const attachment = result.attachments.find((item) => item.name === "m1-metrics");
    if (attachment?.body) {
      this.metrics = JSON.parse(attachment.body.toString("utf8")) as Metrics;
    }
  }

  async onEnd(result: FullResult): Promise<void> {
    const reportPath = process.env.E2E_REPORT_PATH;
    if (!reportPath) throw new Error("E2E_REPORT_PATH is required");
    const report = JSON.parse(readFileSync(reportPath, "utf8")) as ReturnType<
      typeof finalizeReport
    >;
    const passed =
      result.status === "passed" &&
      this.passedTests === report.tests.expected &&
      this.failedTests === 0;
    await writeReport(
      reportPath,
      finalizeReport(report, {
        status: passed ? "passed" : "failed",
        passedTests: this.passedTests,
        failedTests: this.failedTests,
        metrics: this.metrics,
        failure: passed
          ? null
          : { stage: "playwright", message: this.failureMessage ?? `Run ended ${result.status}` },
      }),
    );
  }
}
