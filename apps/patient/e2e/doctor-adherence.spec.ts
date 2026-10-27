import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";
import { emptyVisitSummary, mockSessionApi, user } from "./session-api-mock";

/**
 * «Приём» по кадру «Врач v4 · 07» (спека #34, раздел 6) на подменённом API. Пациентка
 * и данные выдуманы: Авиандр 20 мг 2 раза в сутки с 05.10, пропущены вечера 9.10 и 14.10.
 */

const DOCTOR = user("doctor");
const LEBEDEVA = "00000000-0000-4000-8000-0000000000e7";
const PID = "rx-1";
const NOW = new Date("2026-10-18T15:00:00+03:00");

function adherence(): Schemas["AdherenceOut"] {
  const days = Array.from({ length: 14 }, (_, i) => {
    const day = `2026-10-${String(5 + i).padStart(2, "0")}`;
    const missed = day === "2026-10-09" || day === "2026-10-14";
    return {
      day,
      doses: [
        { prescription_id: PID, slot: 0, status: "taken" as const, marked_at: `${day}T05:40:00Z` },
        {
          prescription_id: PID,
          slot: 1,
          status: missed ? ("missed" as const) : ("taken" as const),
          marked_at: `${day}T19:10:00Z`,
        },
      ],
    };
  });
  return {
    period: { start: "2026-10-05", end: "2026-10-18" },
    course: "active",
    prescriptions: [
      {
        id: PID,
        trade_name: "Авиандр",
        dose_mg: 20,
        times_per_day: 2,
        schedule: "утром и вечером",
        slot_labels: ["Утро", "Вечер"],
        started_on: "2026-10-05",
        ends_on: null,
        stopped_on: null,
      },
    ],
    days,
    planned: 28,
    taken: 26,
    missed: 2,
    unmarked: 0,
    marked_any: true,
  };
}

function finding(id: string, status: string, created_at: string, payload: object) {
  return {
    id,
    patient_id: LEBEDEVA,
    kind: "freetext.finding",
    origin_type: "llm",
    origin_ref: null,
    status,
    created_at,
    decided_by: null,
    decided_at: null,
    decision_comment: null,
    payload: { category: "adherence", ...payload },
  };
}

const FINDINGS = [
  finding("f-missed", "proposed", "2026-10-14T19:10:00Z", {
    summary: "Пропуск вечерней дозы",
    adherence_event: "missed",
    quote: "Вечером уснула и не выпила таблетку.",
    source_type: "diary",
  }),
  finding("f-taken", "confirmed", "2026-10-15T05:40:00Z", {
    summary: "Принимает за 30 минут до завтрака",
    adherence_event: "taken",
    quote: "Таблетку пью утром, за полчаса до завтрака.",
    source_type: "message",
  }),
];

function patient(): Schemas["PatientSummary"] {
  return {
    access: "lead",
    id: LEBEDEVA,
    full_name: "Лебедева Марина Викторовна",
    email: "p@example.invalid",
    phone: null,
    lead_doctor_id: DOCTOR.id,
    created_at: "2026-09-22T08:00:00Z",
    birth_date: "1992-03-14",
    sex: "female",
    pregnant: null,
    breastfeeding: null,
    child_pugh: null,
    egfr: null,
    egfr_measured_on: null,
  };
}

async function mockAdherence(context: BrowserContext, body: Schemas["AdherenceOut"]) {
  await context.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (() => {
      if (path === "/api/v1/patients") return [patient()];
      if (path === `/api/v1/patients/${LEBEDEVA}`) return patient();
      if (path === "/api/v1/alerts" || path === "/api/v1/messaging/unread") return [];
      if (path === `/api/v1/patients/${LEBEDEVA}/adherence`) return body;
      if (path === `/api/v1/patients/${LEBEDEVA}/summary`) return emptyVisitSummary();
      if (path === `/api/v1/patients/${LEBEDEVA}/proposals`) return FINDINGS;
      if (path === `/api/v1/patients/${LEBEDEVA}/assessments`) {
        return { items: [], next_cursor: null };
      }
      if (path.startsWith(`/api/v1/patients/${LEBEDEVA}/`)) return [];
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
}

async function open(page: Page, body: Schemas["AdherenceOut"] = adherence()) {
  await page.clock.setFixedTime(NOW);
  await page.setViewportSize({ width: 1280, height: 1046 });
  await mockSessionApi(page.context(), "doctor");
  await mockAdherence(page.context(), body);
  await page.goto(`/doctor/patients/${LEBEDEVA}/adherence`);
}

test("share has its denominator, period and source; misses and schedule are in words", async ({
  page,
}, testInfo) => {
  await open(page);

  await expect(page.getByText("Приём за 14 дней, 5–18 октября")).toBeVisible();
  await expect(page.getByText("93%", { exact: true })).toBeVisible();
  await expect(page.getByText("26 из 28 доз")).toBeVisible();
  await expect(page.getByText("за 14 дней, по отметкам пациентки")).toBeVisible();
  await expect(page.getByText("2 вечерние дозы: 9.10 и 14.10")).toBeVisible();
  await expect(
    page.getByText("Авиандр 20 мг, 2 раза в сутки, утром и вечером, с 05.10"),
  ).toBeVisible();
  await expect(page.getByRole("radio", { name: "Приём" })).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("07-adherence.png"), fullPage: true });
});

test("calendar moves by arrows and tells the dose in words", async ({ page }) => {
  await open(page);

  await page
    .getByRole("button", { name: "18 октября, утро, принята, по отметке пациентки" })
    .focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");

  const cell = page.getByRole("button", {
    name: "14 октября, вечер, пропущена, по отметке пациентки",
  });
  await expect(cell).toBeFocused();
  await expect(page.getByRole("tooltip")).toContainText("Пропущена, отмечено в 22:10");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(page.getByRole("table")).toContainText("пропущена");
});

test("patient records: waiting one is dashed with «Разобрать», confirmed one says so", async ({
  page,
}) => {
  await open(page);

  await expect(page.getByText("Из записей пациентки о приёме")).toBeVisible();
  await expect(page.getByText("Пропуск вечерней дозы")).toBeVisible();
  await expect(page.getByText("Принимает за 30 минут до завтрака")).toBeVisible();
  await expect(page.getByText("Подтверждено врачом", { exact: false }).first()).toBeVisible();
  await page.getByRole("link", { name: "Разобрать" }).click();
  await expect(page).toHaveURL(new RegExp(`/patients/${LEBEDEVA}/proposals\\?proposal=f-missed`));
});

test("without marks the share is not shown", async ({ page }) => {
  const body = adherence();
  body.days = body.days.map((d) => ({
    ...d,
    doses: d.doses.map((x) => ({ ...x, status: null, marked_at: null })),
  }));
  await open(page, { ...body, taken: 0, missed: 0, unmarked: 28, marked_any: false });

  await expect(page.getByText("Пациентка ещё не отмечала приём")).toBeVisible();
  await expect(page.getByText("93%")).toHaveCount(0);
});

test("without prescriptions it links to therapy", async ({ page }) => {
  await open(page, {
    period: null,
    course: "none",
    prescriptions: [],
    days: [],
    planned: 0,
    taken: 0,
    missed: 0,
    unmarked: 0,
    marked_any: false,
  });

  await expect(page.getByText("Нет действующих назначений")).toBeVisible();
  await expect(page.getByRole("link", { name: "Открыть «Терапию»" })).toBeVisible();
});

test("overview state has the adherence row", async ({ page }) => {
  await open(page);
  await page.goto(`/doctor/patients/${LEBEDEVA}`);

  const row = page.getByRole("link", { name: /Приём.*26 из 28 доз за 14 дней/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("93%");
});
