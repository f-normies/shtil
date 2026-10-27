import type { Schemas } from "@compass/api-client";
import { type BrowserContext, expect, type Locator, type Page, test } from "@playwright/test";
import { mockSessionApi, user } from "./session-api-mock";

/**
 * Вопрос врача к базе знаний в «Терапии» (спека #108) на подменённом API: ответ с цитатами,
 * текст раздела по цитате, пустой ответ, ошибки и повтор. Все данные и тексты выдуманы.
 */

type Answer = Schemas["Answer"];
type Reply = (route: Parameters<Parameters<BrowserContext["route"]>[1]>[0]) => Promise<void>;

const DOCTOR = user("doctor");
const PATIENT = "00000000-0000-4000-8000-0000000000c4";
const SOURCE = "kr-synthetic";
const FRAGMENT = "00000000-0000-4000-8000-0000000000f4";
const QUOTE = "Синтетин назначают взрослым на срок не менее 6 месяцев.";
const STATEMENT = "Синтетин продолжают не меньше полугода.";
const QUESTION = "Сколько продолжать синтетин?";

const ANSWER: Answer = {
  dropped: 0,
  run_id: "00000000-0000-4000-8000-0000000000e1",
  claims: [
    {
      statement: STATEMENT,
      quote: QUOTE,
      fragment: {
        id: FRAGMENT,
        source_id: SOURCE,
        title: "Синтетическое расстройство",
        key: "k",
        section: "doc_3",
        section_title: "3.1. Медикаментозное лечение",
        status: "active",
        version: "100_1",
        uur: "A",
        udd: 1,
      },
    },
  ],
};

const SOURCE_VIEW: Schemas["SourceView"] = {
  source: {
    id: SOURCE,
    title: "Синтетическое расстройство",
    kind: "clinical_guideline",
    publisher: "Синтетический издатель",
    version: "100_1",
    date: "2026-10-14",
    url: "https://example.invalid/kr",
    supplementary: false,
  },
  registry: null,
  locator: null,
  found: true,
  sections: [
    {
      number: null,
      title: "3.1. Медикаментозное лечение",
      fragments: [
        {
          id: FRAGMENT,
          key: "k",
          document: {
            id: "00000000-0000-4000-8000-0000000000f5",
            source_id: SOURCE,
            title: "Синтетическое расстройство",
            version: "100_1",
            url: "https://example.invalid/kr",
            kind: "clinical_guideline",
            status: "active",
          },
          section: "doc_3",
          section_title: "3.1. Медикаментозное лечение",
          heading_path: "3. Лечение > 3.1. Медикаментозное лечение",
          kind: "text",
          text: `Вводная часть раздела. ${QUOTE} Заключительная часть.`,
          uur: "A",
          udd: 1,
        },
      ],
    },
  ],
};

const problem =
  (status: number): Reply =>
  (route) =>
    route.fulfill({ status, json: { type: "about:blank", title: "Сбой", status } });
const answer =
  (body: Answer): Reply =>
  (route) =>
    route.fulfill({ json: body });

/**
 * Подмена API «Терапии». Ответы на вопросы — по очереди из `replies`; тела вопросов
 * записываются. Справка по КР — без диагнозов: на экране только кнопка вопроса.
 */
async function mockTherapy(context: BrowserContext, replies: Reply[]) {
  const asked: unknown[] = [];
  await context.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/knowledge/answers") {
      asked.push(route.request().postDataJSON());
      const reply = replies.shift();
      await (reply ? reply(route) : problem(500)(route));
      return;
    }
    if (path === `/api/v1/knowledge/sources/${SOURCE}`) {
      await route.fulfill({ json: SOURCE_VIEW });
      return;
    }
    const json = (() => {
      if (path === "/api/v1/alerts") return [];
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
      if (path.endsWith("/therapy/guidance")) {
        return {
          status: null,
          stale: false,
          computed_at: null,
          catalog_drugs: [],
          guidelines: [],
          profile: null,
          claims: [],
        } satisfies Schemas["GuidanceOut"];
      }
      if (/\/(diagnoses|medications|prescriptions|reactions|proposals|alerts)$/.test(path)) {
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
  return asked;
}

/** Панель въезжает 200 мс, содержимое проявляется 150 мс: ждём конца анимаций. */
async function settled(panel: Locator) {
  await panel.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)),
  );
}

async function openQuestion(page: Page, replies: Reply[]) {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockSessionApi(page.context(), "doctor");
  const asked = await mockTherapy(page.context(), replies);
  await page.goto(`/doctor/patients/${PATIENT}/therapy`);
  const opener = page.getByRole("button", { name: "Спросить по КР" });
  await opener.click();
  const inspector = page.getByRole("complementary", { name: "Вопрос к базе знаний" });
  await expect(inspector).toBeVisible();
  return {
    asked,
    opener,
    inspector,
    field: inspector.getByLabel("Вопрос по клиническим рекомендациям"),
  };
}

test("question gets an answer with quotes; a quote opens the section and ‹ Ответ returns", async ({
  page,
}) => {
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const { asked, inspector, field } = await openQuestion(page, [
    async (route) => {
      await held;
      await answer(ANSWER)(route);
    },
  ]);
  const ask = inspector.getByRole("button", { name: "Спросить" });
  const caption = inspector.getByText("Справка по базе знаний. Решение о терапии принимает врач.");
  // Первое открытие ставит фокус сразу в поле вопроса.
  await expect(field).toBeFocused();
  await expect(caption).toBeVisible();
  await expect(inspector.getByText("Без ФИО и других данных пациента")).toBeVisible();
  await expect(ask).toBeDisabled();

  // Shift+Enter переносит строку, Enter отправляет.
  await field.fill(`  ${QUESTION}`);
  await field.press("Shift+Enter");
  await expect(field).toHaveValue(`  ${QUESTION}\n`);
  expect(asked).toEqual([]);
  await field.press("Enter");

  await expect(inspector.getByText("Готовим ответ…")).toBeVisible();
  await expect(inspector.getByText(QUESTION, { exact: true })).toBeVisible();
  await expect(field).toBeDisabled();
  await expect(ask).toBeDisabled();
  await expect(caption).toBeVisible();
  release();

  const live = inspector.locator('[aria-live="polite"]');
  await expect(live.getByText(STATEMENT)).toBeVisible();
  await expect(
    live.getByText("КР «Синтетическое расстройство», 3.1. Медикаментозное лечение"),
  ).toBeVisible();
  await expect(live.getByText("УУР A, УДД 1")).toBeVisible();
  await expect(caption).toBeVisible();
  await expect(field).toBeEnabled();
  await expect(field).toHaveValue("");
  // Вопрос без пробелов по краям и без source_ids: поиск по всему корпусу врача.
  expect(asked).toEqual([{ question: QUESTION }]);
  await settled(inspector);
  expect(await inspector.boundingBox()).toMatchObject({ x: 960, width: 320 });
  await page.screenshot({ path: test.info().outputPath("question-answer-1280.png") });

  const quote = inspector.getByRole("button", { name: new RegExp(QUOTE) });
  await quote.click();
  const back = inspector.getByRole("button", { name: "Ответ" });
  await expect(back).toBeFocused();
  await expect(inspector.locator("mark")).toHaveText(QUOTE);
  await expect(
    inspector.getByText("Клинические рекомендации «Синтетическое расстройство»"),
  ).toBeVisible();
  await expect(inspector.getByText(STATEMENT)).toBeHidden();
  // Ответ скрыт вместе со своей подписью: видна подпись вида раздела.
  await expect(caption.filter({ visible: true })).toHaveCount(1);
  await settled(inspector);
  await page.screenshot({ path: test.info().outputPath("question-section-1280.png") });

  await back.click();
  await expect(inspector.getByText(STATEMENT)).toBeVisible();
  await expect(quote).toBeFocused();
});

test("empty answer replaces the previous one; closing the inspector resets the question", async ({
  page,
}) => {
  const { asked, opener, inspector, field } = await openQuestion(page, [
    answer(ANSWER),
    answer({ claims: [], dropped: 0, run_id: null }),
  ]);
  await field.fill(QUESTION);
  await inspector.getByRole("button", { name: "Спросить" }).click();
  await expect(inspector.getByText(STATEMENT)).toBeVisible();

  // Повторный клик «Спросить по КР» не пересоздаёт панель: ответ на месте, фокус в поле.
  await opener.click();
  await expect(inspector.getByText(STATEMENT)).toBeVisible();
  await expect(field).toBeFocused();
  expect(asked).toEqual([{ question: QUESTION }]);

  await field.fill("Чем заменить синтетин?");
  await field.press("Enter");
  await expect(inspector.getByText("В базе знаний ответа нет")).toBeVisible();
  await expect(inspector.getByText(STATEMENT)).toBeHidden();
  await expect(inspector.getByText("Чем заменить синтетин?", { exact: true })).toBeVisible();
  await expect(
    inspector.getByText("Справка по базе знаний. Решение о терапии принимает врач."),
  ).toBeVisible();
  await expect(field).toBeEnabled();
  expect(asked).toEqual([{ question: QUESTION }, { question: "Чем заменить синтетин?" }]);

  await inspector.getByRole("button", { name: "Закрыть" }).click();
  await expect(inspector).toBeHidden();
  await expect(opener).toBeFocused();
  await opener.click();
  await expect(inspector.getByText("В базе знаний ответа нет")).toBeHidden();
  await expect(field).toHaveValue("");
});

test("502, 503 and a network failure say the reference is unavailable; Повторить asks again", async ({
  page,
}) => {
  const { asked, inspector, field } = await openQuestion(page, [
    problem(502),
    problem(503),
    (route) => route.abort("failed"),
    answer(ANSWER),
  ]);
  const unavailable = inspector.getByText("Справка недоступна, повторите позже");
  const retry = inspector.getByRole("button", { name: "Повторить" });
  await field.fill(QUESTION);
  await field.press("Enter");

  for (const _ of ["502", "503", "сеть"]) {
    await expect(unavailable).toBeVisible();
    await expect(
      inspector.getByText("Справка по базе знаний. Решение о терапии принимает врач."),
    ).toBeVisible();
    await expect(field).toBeHidden();
    await expect(retry).toBeFocused();
    await retry.click();
  }

  await expect(inspector.getByText(STATEMENT)).toBeVisible();
  await expect(unavailable).toBeHidden();
  expect(asked).toEqual(Array(4).fill({ question: QUESTION }));
});
