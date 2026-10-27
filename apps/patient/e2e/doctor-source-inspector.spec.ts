import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Locator, type Page, test } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * Инспектор источника кабинета врача (спека #106) на подменённом API: сигнал, лист
 * направления, проверка терапии и утверждение справки. Все данные и тексты выдуманы.
 */

type SourceView = Schemas["SourceView"];

const DOCTOR = user("doctor");
const PATIENT = "00000000-0000-4000-8000-0000000000c3";
const KR = {
  source: "kr-synthetic",
  locator: "раздел 2.5",
  title: "Синтетическое расстройство",
  url: "https://example.invalid/kr",
};
const LABEL = {
  source: "synthetin-label",
  locator: "4.1: синтетическое расстройство",
  title: "СИНТЕТИН: общая характеристика",
  url: "https://example.invalid/register",
};
const QUOTE = "Синтетин назначают взрослым.";

const ALERT: Schemas["AlertOut"] = {
  as_deputy: false,
  escalation_step: null,
  id: "routing",
  patient_id: PATIENT,
  doctor_id: DOCTOR.id,
  origin_type: "rule",
  rule_id: "routing-synthetic",
  rule_version: 3,
  kind: "routing",
  level: "desirable",
  title: "Вероятное синтетическое расстройство",
  doctor_actions: ["Обсудить консультацию психиатра"],
  sources: [KR],
  occurrences: 1,
  created_at: "2026-10-21T15:20:00Z",
  last_triggered_at: "2026-10-21T15:20:00Z",
  delivered_at: "2026-10-21T15:20:02Z",
  delivered_via: "ws",
  acknowledged_at: null,
  acknowledged_by: null,
  state: "new",
  snoozed_until: null,
  resolved_at: null,
  resolved_by: null,
  status: "delivered",
  triggers: [
    {
      at: "2026-10-21T15:20:00Z",
      origin_ref: "s",
      rule_version: 3,
      level: "desirable",
      evidence: { "phq-9": { total: 16 } },
    },
  ],
  actions: [],
  actors: {},
  decision: null,
};

function sourceView(kind: "clinical_guideline" | "drug_label", text: string): SourceView {
  const label = kind === "drug_label";
  const id = label ? LABEL.source : KR.source;
  const document = {
    id: "00000000-0000-4000-8000-0000000000f1",
    source_id: id,
    title: label ? LABEL.title : KR.title,
    version: label ? "0002" : "100_1",
    url: label ? LABEL.url : KR.url,
    kind,
    status: label ? ("active" as const) : ("superseded" as const),
  };
  return {
    source: {
      id,
      title: document.title,
      kind,
      publisher: "Синтетический издатель",
      version: "0002",
      date: "2026-10-14",
      url: document.url,
      supplementary: false,
    },
    registry: label
      ? { reg_number: "ЛП-000000", status: "active", snapshot_date: "2026-10-16" }
      : null,
    locator: label ? LABEL.locator : null,
    found: true,
    sections: [
      {
        number: label ? "4.1" : null,
        title: "Показания",
        fragments: [
          {
            id: "00000000-0000-4000-8000-0000000000f2",
            key: "k",
            document,
            section: "4.1",
            section_title: "Показания",
            heading_path: "4. Клинические данные > 4.1 Показания",
            kind: "text",
            text,
            udd: null,
            uur: null,
          },
        ],
      },
    ],
  };
}

const OPTION: Schemas["OptionOut"] = {
  drug_id: "synthetin",
  trade_name: "Синтетин",
  inn: "синтетин",
  indication_id: "synthetic",
  indication_title: "Синтетическое расстройство",
  indication_icd10: ["F41.1"],
  regimen: {
    form: "таблетки 20 мг",
    dose_mg: 20,
    strength_mg: 20,
    times_per_day: 2,
    schedule: "утром и вечером",
    duration_days: null,
    max_days: null,
    duration_text: "определяет врач",
    source: { ...LABEL, locator: "4.2" },
  },
  instructions: [],
  checks: [
    {
      id: "indication",
      title: "Показание",
      status: "pass",
      detail: "F41.1 у пациентки",
      source: LABEL,
      actions: [],
    },
  ],
  proposal: null,
};

const GUIDANCE: Schemas["GuidanceOut"] = {
  status: "done",
  stale: false,
  computed_at: "2026-10-22T09:40:00Z",
  catalog_drugs: ["Синтетин"],
  guidelines: [{ id: KR.source, title: KR.title }],
  profile: { diagnoses: ["F41.1"], sex: "female", age_group: "18–64" },
  claims: [
    {
      statement: "Синтетин подходит взрослым",
      quote: QUOTE,
      fragment: {
        id: "00000000-0000-4000-8000-0000000000f2",
        source_id: KR.source,
        title: KR.title,
        key: "k",
        section: "doc_3",
        section_title: "3. Лечение",
        status: "superseded",
        version: "100_1",
        uur: "A",
        udd: 1,
      },
    },
  ],
};

/** Подмена API экранов «Сигналы» и «Терапия». Запросы источника записываются. */
async function mockDoctor(context: BrowserContext) {
  const sources: string[] = [];
  await context.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.startsWith("/api/v1/knowledge/sources/")) {
      sources.push(`${path}${url.search}`);
      const label = path.endsWith(LABEL.source);
      const text = label ? "Синтетин показан при синтетическом расстройстве." : `Вводная. ${QUOTE}`;
      await route.fulfill({ json: sourceView(label ? "drug_label" : "clinical_guideline", text) });
      return;
    }
    if (path.endsWith("/therapy/options")) {
      await route.fulfill({ json: { options: [OPTION], message: null } });
      return;
    }
    if (path.startsWith("/api/v1/assessments/scales/")) {
      await route.fulfill({ status: 404, json: { status: 404, title: "Not found" } });
      return;
    }
    const json = (() => {
      if (path === "/api/v1/alerts") {
        return url.searchParams.get("status") === "resolved" ? [] : [ALERT];
      }
      if (path === "/api/v1/alerts/decisions") {
        return { safety: [], no_contact: "", therapy: [], referral_decline: [], snooze: null };
      }
      if (path === "/api/v1/patients") return [{ id: PATIENT, full_name: "Орлова Синтия" }];
      if (path === `/api/v1/patients/${PATIENT}`) {
        return {
          id: PATIENT,
          full_name: "Орлова Синтия",
          email: "p@example.invalid",
          lead_doctor_id: DOCTOR.id,
          created_at: "2026-09-22T09:00:00Z",
          phone: null,
          birth_date: "1988-05-01",
          sex: "female",
        };
      }
      if (path.endsWith("/assessments")) return { items: [], next_cursor: null };
      if (path.endsWith("/diagnoses")) {
        return [
          {
            id: "00000000-0000-4000-8000-0000000000d9",
            icd10: "F41.1",
            title: "Синтетическое расстройство",
            set_at: "2026-10-01T09:00:00Z",
            set_by: DOCTOR.id,
            resolved_at: null,
            resolved_by: null,
          },
        ];
      }
      if (path.endsWith("/therapy/guidance")) return GUIDANCE;
      if (/\/(alerts|thresholds|medications|prescriptions|reactions|proposals)$/.test(path)) {
        return [];
      }
      return null;
    })();
    if (json === null) {
      await route.fallback();
      return;
    }
    await route.fulfill({ json });
  });
  return sources;
}

/** Панель въезжает 200 мс, содержимое проявляется 150 мс: ждём конца анимаций. */
async function settled(panel: Locator) {
  await panel.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)),
  );
}

async function setup(page: Page) {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "doctor");
  return mockDoctor(page.context());
}

test("rule signal opens its basis beside the details; Esc closes and returns focus", async ({
  page,
}) => {
  const sources = await setup(page);
  await page.goto("/doctor/alerts");
  await page.getByRole("link", { name: /Орлова/ }).click();

  const opener = page.getByRole("button", { name: "Открыть основание" });
  await opener.click();
  const inspector = page.getByRole("complementary", { name: "Источник" });
  await expect(inspector).toBeVisible();
  await expect(
    inspector.getByText("Правило «Вероятное синтетическое расстройство», версия 3"),
  ).toBeVisible();
  await expect(inspector.getByText("PHQ-9 · сумма: 16")).toBeVisible();
  await expect(
    inspector.getByText("Клинические рекомендации «Синтетическое расстройство»"),
  ).toBeVisible();
  await expect(inspector.getByText("Прежняя версия документа. Текст — версия 100_1")).toBeVisible();
  expect(sources).toEqual([
    `/api/v1/knowledge/sources/${KR.source}?locator=${encodeURIComponent("раздел 2.5")}`,
  ]);
  // Панель 320 px справа сдвигает подробности и не перекрывает их.
  await settled(inspector);
  expect(await inspector.boundingBox()).toMatchObject({ x: 960, width: 320 });
  const details = await page.locator("article").first().boundingBox();
  expect((details?.x ?? 0) + (details?.width ?? 0)).toBeLessThanOrEqual(960);
  await page.screenshot({ path: test.info().outputPath("inspector-signal-1280.png") });

  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
  await expect(opener).toBeFocused();
});

test("inspector opens over the referral sheet; Close and Esc return focus into the sheet", async ({
  page,
}) => {
  await setup(page);
  await page.goto("/doctor/alerts");
  await page.getByRole("link", { name: /Орлова/ }).click();
  await page.getByRole("button", { name: "Направить к психиатру" }).click();
  const sheet = page.getByRole("dialog", { name: "Направление к психиатру" });
  const opener = sheet.getByRole("button", { name: "Открыть основание" });

  await opener.click();
  const inspector = page.getByRole("dialog", { name: "Источник" });
  await expect(inspector.getByText("Текст раздела", { exact: true })).toBeVisible();
  // «Закрыть» закрывает окно инспектора штатно: лист снова активен и получает фокус.
  await inspector.getByRole("button", { name: "Закрыть" }).click();
  await expect(inspector).toBeHidden();
  await expect(opener).toBeFocused();

  await opener.click();
  await expect(inspector).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
  await expect(sheet).toBeVisible();
  await expect(opener).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
});

test("therapy check and guidance claim open their sources in one inspector", async ({ page }) => {
  const sources = await setup(page);
  await page.goto(`/doctor/patients/${PATIENT}/therapy`);
  await page.getByRole("button", { name: "Подобрать терапию" }).click();
  await page.getByRole("option", { name: /Показание/ }).click();

  await page.getByRole("button", { name: `«${LABEL.title}», ${LABEL.locator}` }).click();
  const inspector = page.getByRole("complementary", { name: "Источник" });
  await expect(inspector.getByText("Проверка «Показание»")).toBeVisible();
  await expect(inspector.getByText("F41.1 у пациентки")).toBeVisible();
  await expect(inspector.getByText("ЛП-000000, действует по ГРЛС на 16.10.2026")).toBeVisible();
  await expect(inspector.getByRole("link", { name: /Открыть в реестре/ })).toHaveAttribute(
    "target",
    "_blank",
  );
  await settled(inspector);
  await page.screenshot({ path: test.info().outputPath("inspector-check-1280.png") });

  const quote = page.getByRole("button", { name: new RegExp(QUOTE) });
  await quote.click();
  await expect(inspector.getByText("Утверждение справки")).toBeVisible();
  await expect(inspector.getByText("Диагнозы: F41.1")).toBeVisible();
  await expect(inspector.getByText("Возраст: 18–64 года")).toBeVisible();
  await expect(inspector.locator("mark")).toHaveText(QUOTE);
  expect(sources.at(-1)).toBe(
    `/api/v1/knowledge/sources/${KR.source}?fragment_id=${GUIDANCE.claims[0]?.fragment.id}`,
  );
  await settled(inspector);
  await page.screenshot({ path: test.info().outputPath("inspector-claim-1280.png") });

  await inspector.getByRole("button", { name: "Закрыть" }).click();
  await expect(inspector).toBeHidden();
  await expect(quote).toBeFocused();
});

test("saving a changed clinical profile closes the previous source applicability", async ({
  page,
}) => {
  await setup(page);
  await page.route(`**/api/v1/patients/${PATIENT}/profile`, (route) => route.fulfill({ json: {} }));
  await page.goto(`/doctor/patients/${PATIENT}/therapy`);
  await page.getByRole("button", { name: new RegExp(QUOTE) }).click();
  const inspector = page.getByRole("complementary", { name: "Источник" });
  await expect(inspector.getByText("Диагнозы: F41.1")).toBeVisible();

  await page.getByRole("combobox", { name: /^Пол/ }).selectOption("male");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();

  await expect(page.getByText("Сохранено", { exact: true })).toBeVisible();
  await expect(inspector).toBeHidden();
});
