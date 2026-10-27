import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { runIssue31IntegrationEvidence } from "./issue31-integration.js";

type Role = "doctor" | "patient";
type ActionCounts = Record<Role, { count: number; steps: string[] }>;
type RealtimeEvent = { type: string; data: Record<string, unknown> };
type AlertWatchWindow = Window & { m1AlertShownAt?: Promise<number> };

const APP_ORIGIN = "http://127.0.0.1:5173";
const TEST_PASSWORD = "Compass-E2E-21";
const ALERT_TITLE = "Мысли о смерти или самоповреждении (PHQ-9, пункт 9)";
const ALERT_WAIT_MS = 20_000;

/**
 * Starts watching the doctor page for the alert card. The page records the wall-clock time
 * of the DOM change itself, so test-side waits and Playwright polling do not inflate the metric.
 */
async function watchAlertCard(page: Page): Promise<void> {
  await page.evaluate(
    ({ title, timeoutMs }) => {
      const shown = () =>
        Array.from(document.querySelectorAll("article h3")).some(
          (heading) => heading.textContent === title,
        );
      (window as AlertWatchWindow).m1AlertShownAt = new Promise<number>((resolve, reject) => {
        if (shown()) {
          reject(new Error(`alert card "${title}" was already shown before submission`));
          return;
        }
        const timer = window.setTimeout(() => {
          observer.disconnect();
          reject(new Error(`alert card "${title}" did not appear within ${timeoutMs} ms`));
        }, timeoutMs);
        const observer = new MutationObserver(() => {
          if (!shown()) return;
          observer.disconnect();
          window.clearTimeout(timer);
          resolve(Date.now());
        });
        observer.observe(document.body, { childList: true, subtree: true, characterData: true });
      });
    },
    { title: ALERT_TITLE, timeoutMs: ALERT_WAIT_MS },
  );
}

async function scaleValue(page: Page, scale: string): Promise<number> {
  const value = await page
    .getByRole("region", { name: "Состояние", exact: true })
    .getByRole("listitem")
    .filter({ has: page.getByText(scale, { exact: true }) })
    .locator("p")
    .last()
    .textContent();
  return Number(value?.match(/^\s*(\d+)\s*из \d+\s*$/)?.[1] ?? Number.NaN);
}

async function act(
  counts: ActionCounts,
  role: Role,
  name: string,
  action: () => Promise<unknown>,
): Promise<void> {
  await action();
  counts[role].steps.push(name);
  counts[role].count += 1;
}

async function login(page: Page, counts: ActionCounts, role: Role, email: string): Promise<void> {
  await act(counts, role, "Ввести email", () =>
    page.getByLabel("Электронная почта", { exact: true }).fill(email),
  );
  await act(counts, role, "Ввести пароль", () =>
    page.getByLabel("Пароль", { exact: true }).fill(TEST_PASSWORD),
  );
  await act(counts, role, "Войти", () => page.getByRole("button", { name: "Войти" }).click());
}

async function apiJson<T>(context: BrowserContext, path: string): Promise<T> {
  const response = await context.request.get(`${APP_ORIGIN}${path}`);
  expect(response.ok(), `GET ${path} should succeed`).toBeTruthy();
  return (await response.json()) as T;
}

/** Read every history page: the API orders by receipt, the assertion by completion. */
async function historyScores(
  context: BrowserContext,
  patientId: string,
  scale: string,
): Promise<number[]> {
  type Result = {
    patient_id: string;
    scale_id: string;
    completed_at: string;
    scores: Record<string, { value: number }>;
  };
  const results: Result[] = [];
  let before: string | null = null;
  do {
    const query = new URLSearchParams({ scale_id: scale, limit: "100" });
    if (before) query.set("before", before);
    const page = await apiJson<{ items: Result[]; next_cursor: string | null }>(
      context,
      `/api/v1/patients/${patientId}/assessments?${query}`,
    );
    for (const result of page.items) {
      expect(result.patient_id).toBe(patientId);
      expect(result.scale_id).toBe(scale);
      results.push(result);
    }
    before = page.next_cursor;
  } while (before);
  return results
    .sort((a, b) => Date.parse(a.completed_at) - Date.parse(b.completed_at))
    .map((result) => result.scores.total.value);
}

test("M1: patient submits PHQ-9, doctor receives and acknowledges the critical alert", async ({
  browser,
}, testInfo) => {
  const counts: ActionCounts = {
    doctor: { count: 0, steps: [] },
    patient: { count: 0, steps: [] },
  };
  const events: RealtimeEvent[] = [];
  let doctorContext: BrowserContext | undefined;
  let patientContext: BrowserContext | undefined;
  let doctorLoadEvents = 0;
  let interactionStarted: number | null = null;
  let interactionEnded: number | null = null;
  let submissionStarted: number | null = null;
  let alertObserved: number | null = null;

  try {
    const doctorBrowserContext = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      serviceWorkers: "block",
    });
    doctorContext = doctorBrowserContext;
    const patientBrowserContext = await browser.newContext({
      viewport: { width: 360, height: 800 },
      serviceWorkers: "block",
    });
    patientContext = patientBrowserContext;
    const doctorPage = await doctorBrowserContext.newPage();
    const patientPage = await patientBrowserContext.newPage();
    doctorPage.on("load", () => {
      doctorLoadEvents += 1;
    });

    doctorPage.on("websocket", (socket) => {
      if (!socket.url().endsWith("/api/v1/ws")) return;
      socket.on("framereceived", ({ payload }) => {
        if (typeof payload !== "string") return;
        try {
          const message = JSON.parse(payload) as { type?: unknown; data?: unknown };
          if (typeof message.type === "string") {
            events.push({
              type: message.type,
              data:
                typeof message.data === "object" && message.data !== null
                  ? (message.data as Record<string, unknown>)
                  : {},
            });
          }
        } catch {
          // The stream can contain control frames; only JSON application events are recorded.
        }
      });
    });

    await Promise.all([
      doctorPage.goto(`${APP_ORIGIN}/doctor/login`),
      patientPage.goto(`${APP_ORIGIN}/patient/login`),
    ]);
    interactionStarted = performance.now();

    await login(doctorPage, counts, "doctor", "doctor@example.com");
    await expect(doctorPage.getByRole("heading", { name: "Пациенты", exact: true })).toBeVisible();
    const patientRow = doctorPage
      .getByRole("listbox", { name: "Пациенты" })
      .getByRole("option", { name: /Лебедева Марина Викторовна/ });
    await expect(patientRow).toBeVisible();
    await act(counts, "doctor", "Открыть карточку пациентки", () => patientRow.dblclick());
    await expect(doctorPage.getByRole("region", { name: "Состояние", exact: true })).toBeVisible();

    // The overview shows the latest scores; full histories are checked through API below.
    await expect.poll(() => scaleValue(doctorPage, "GAD-7")).toBe(9);
    await expect.poll(() => scaleValue(doctorPage, "PHQ-9")).toBe(6);
    const alertCard = doctorPage
      .getByRole("article")
      .filter({ has: doctorPage.getByRole("heading", { name: ALERT_TITLE }) });
    await expect(alertCard).toHaveCount(0);

    await expect
      .poll(() => events.some((event) => event.type === "ready"), { timeout: 15_000 })
      .toBeTruthy();
    const documentLoadsBeforeSubmission = doctorLoadEvents;

    await login(patientPage, counts, "patient", "patient1@example.com");
    await patientPage.getByRole("button", { name: "Пропустить", exact: true }).click();
    await expect(patientPage.getByRole("heading", { name: "Как вы сейчас?" })).toBeVisible();
    await act(counts, "patient", "Открыть PHQ-9", () =>
      patientPage.goto(`${APP_ORIGIN}/patient/assessments/phq-9`),
    );
    await expect(
      patientPage.getByRole("heading", { name: "Опросник здоровья пациента (PHQ-9)" }),
    ).toBeVisible();

    const answers: Record<string, number> = {
      q1: 1,
      q2: 1,
      q3: 1,
      q4: 1,
      q5: 1,
      q6: 1,
      q7: 0,
      q8: 0,
      q9: 1,
    };
    for (const [question, value] of Object.entries(answers)) {
      await act(counts, "patient", `Ответить на ${question}`, () =>
        patientPage.locator(`input[name="answer-${question}"][value="${value}"]`).check(),
      );
    }
    await expect(patientPage.getByText("Отвечено: 9 из 9")).toBeVisible();

    await watchAlertCard(doctorPage);
    const submitResponsePromise = patientPage.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/assessments") && response.request().method() === "POST",
      { timeout: 30_000 },
    );
    submissionStarted = Date.now();
    await act(counts, "patient", "Отправить ответы", () =>
      patientPage.getByRole("button", { name: "Отправить ответы" }).click(),
    );
    const submission = await submitResponsePromise;
    expect(submission.status()).toBe(201);
    const savedResult = (await submission.json()) as {
      id: string;
      patient_id: string;
      scale_id: string;
      answers: Record<string, number>;
      scores: Record<string, { value: number }>;
    };
    expect(savedResult.scale_id).toBe("phq-9");
    expect(savedResult.answers.q9).toBe(1);
    expect(savedResult.scores.total.value).toBe(7);
    await expect(patientPage.getByText("Результат сохранён на сервере.")).toBeVisible();

    await expect
      .poll(
        () =>
          events.some(
            (event) =>
              event.type === "assessment.completed" && event.data.assessment_id === savedResult.id,
          ) && events.some((event) => ["alert.created", "alert.raised"].includes(event.type)),
        { timeout: ALERT_WAIT_MS },
      )
      .toBeTruthy();

    const alertShownAt = await doctorPage.evaluate(
      () => (window as AlertWatchWindow).m1AlertShownAt ?? Promise.reject(new Error("no watch")),
    );
    expect(alertShownAt - submissionStarted).toBeGreaterThan(0);
    alertObserved = alertShownAt;
    await expect(alertCard).toBeVisible();
    await expect.poll(() => scaleValue(doctorPage, "PHQ-9")).toBe(7);
    expect(doctorLoadEvents).toBe(documentLoadsBeforeSubmission);
    await expect(alertCard.getByText("Обязательно", { exact: true })).toBeVisible();
    await expect(alertCard.getByText("Связаться с пациентом сегодня")).toBeVisible();

    await act(counts, "doctor", "Взять критический сигнал в работу", () =>
      alertCard.getByRole("button", { name: "Взять в работу" }).click(),
    );
    await expect(doctorPage.getByText("Сигнал взят в работу вами", { exact: true })).toBeVisible();
    await expect(alertCard).toBeVisible();
    await expect(alertCard.getByText(/^Взят в работу/)).toBeVisible();
    await expect(alertCard.getByRole("button", { name: "Взять в работу" })).toHaveCount(0);
    await expect(alertCard.getByRole("link", { name: "Зафиксировать решение" })).toBeVisible();
    interactionEnded = performance.now();
    expect(doctorLoadEvents).toBe(documentLoadsBeforeSubmission);

    // API checks run after the measured flow: they verify results and are not user actions.
    const patientId = new URL(doctorPage.url()).pathname.split("/")[3];
    expect(patientId).toBeTruthy();
    expect(savedResult.patient_id).toBe(patientId);
    expect(await historyScores(doctorBrowserContext, patientId, "gad-7")).toEqual([14, 9]);
    expect(await historyScores(doctorBrowserContext, patientId, "phq-9")).toEqual([8, 6, 7]);
    type SavedAlert = {
      id: string;
      rule_id: string | null;
      level: string;
      sources: Array<{ source: string }>;
      delivered_at: string | null;
      delivered_via: string | null;
      acknowledged_at: string | null;
      acknowledged_by: string | null;
      state: string;
    };
    const patientAlertsPath = `/api/v1/patients/${patientId}/alerts`;
    let alert: SavedAlert | undefined;
    await expect
      .poll(
        async () => {
          const alerts = await apiJson<SavedAlert[]>(doctorBrowserContext, patientAlertsPath);
          alert = alerts.find((item) => item.rule_id === "phq9-suicidal-thoughts");
          return alert?.delivered_via ?? null;
        },
        { timeout: 15_000 },
      )
      .toBe("ws");
    expect(alert?.level).toBe("mandatory");
    expect(alert?.sources.some((source) => source.source === "kr-depression-2024")).toBeTruthy();
    expect(alert?.sources.some((source) => source.source === "zolotareva-phq9-2023")).toBeTruthy();
    expect(events.some((event) => String(event.data.alert_id) === alert?.id)).toBeTruthy();
    expect(alert?.delivered_at).not.toBeNull();
    expect(alert?.acknowledged_at).not.toBeNull();
    expect(alert?.state).toBe("in_work");
    const doctor = await apiJson<{ id: string }>(doctorBrowserContext, "/api/v1/auth/me");
    expect(alert?.acknowledged_by).toBe(doctor.id);

    // Issue #31 evidence is collected after the unchanged M1 measured boundary.
    await runIssue31IntegrationEvidence(browser, testInfo);
  } finally {
    const durations = {
      interaction_total:
        interactionStarted !== null && interactionEnded !== null
          ? Math.round(interactionEnded - interactionStarted)
          : null,
      submission_to_alert:
        submissionStarted !== null && alertObserved !== null
          ? Math.round(alertObserved - submissionStarted)
          : null,
    };
    await testInfo.attach("m1-metrics", {
      body: Buffer.from(
        JSON.stringify({
          durations_ms: durations,
          actions: {
            ...counts,
            total: counts.doctor.count + counts.patient.count,
          },
        }),
      ),
      contentType: "application/json",
    });
    await Promise.all([doctorContext?.close(), patientContext?.close()]);
  }
});
