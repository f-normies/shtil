import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const EVIDENCE_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../.superpowers/sdd/2026-10-21-20-shtil-design-system-design/evidence/visual-fixed",
);

test("doctor segments divide 328 pixels equally without clipping labels", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  const group = page.getByRole("radiogroup", { name: "Фильтр списка" });
  const segments = await group.locator(".ui-segmented__option").evaluateAll((options) =>
    options.map((option) => {
      const bounds = option.getBoundingClientRect();
      const label = option.querySelector<HTMLElement>(".ui-segmented__label");
      return {
        width: bounds.width,
        label: label?.textContent,
        clientWidth: label?.clientWidth,
        scrollWidth: label?.scrollWidth,
      };
    }),
  );

  expect(await group.evaluate((element) => element.getBoundingClientRect().width)).toBe(328);
  expect(segments.map(({ label }) => label)).toEqual(["Все", "С сигналами", "Приглашённые"]);
  expect(segments.every(({ width }) => Math.abs(width - 320 / 3) <= 0.02)).toBe(true);
  const widths = segments.map(({ width }) => width);
  expect(Math.max(...widths) - Math.min(...widths)).toBeLessThanOrEqual(0.02);
  expect(segments.every(({ clientWidth, scrollWidth }) => clientWidth === scrollWidth)).toBe(true);
});

test("popover uses the reference padding, no border, and two shadows", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  await page.getByRole("button", { name: "Открыть поповер" }).click();
  const panel = page.getByRole("dialog", { name: "Пояснение" });
  await expect(panel).toBeVisible();
  const style = await panel.evaluate((element) => {
    const computed = getComputedStyle(element);
    return {
      paddingLeft: computed.paddingLeft,
      paddingRight: computed.paddingRight,
      borderLeftWidth: computed.borderLeftWidth,
      borderRightWidth: computed.borderRightWidth,
      boxShadow: computed.boxShadow,
    };
  });

  expect(style).toEqual({
    paddingLeft: "16px",
    paddingRight: "16px",
    borderLeftWidth: "0px",
    borderRightWidth: "0px",
    boxShadow: "rgba(16, 26, 32, 0.12) 0px 12px 16px 0px, rgba(16, 26, 32, 0.08) 0px 2px 3px 0px",
  });
});

test("doctor counters use the reference size, weight, and semantic colors", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  const counters = await page.locator(".ui-counter").evaluateAll((elements) =>
    elements.map((element) => {
      const style = getComputedStyle(element);
      return {
        tone: element.getAttribute("data-tone"),
        fontSize: style.fontSize,
        lineHeight: style.lineHeight,
        fontWeight: style.fontWeight,
        color: style.color,
      };
    }),
  );

  expect(counters).toEqual([
    {
      tone: "neutral",
      fontSize: "14px",
      lineHeight: "20px",
      fontWeight: "400",
      color: "rgb(91, 105, 115)",
    },
    {
      tone: "required",
      fontSize: "14px",
      lineHeight: "20px",
      fontWeight: "600",
      color: "rgb(168, 43, 25)",
    },
    {
      tone: "advised",
      fontSize: "14px",
      lineHeight: "20px",
      fontWeight: "600",
      color: "rgb(122, 68, 0)",
    },
  ]);
  await page.evaluate(() => document.fonts.ready.then(() => true));
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({
    path: resolve(EVIDENCE_DIR, "doctor-counters.png"),
    fullPage: true,
  });
});

test("doctor quiet and link buttons keep a clear background and primary hover text when pressed", async ({
  page,
}) => {
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  for (const id of ["quiet-active", "link-active"]) {
    const button = page.getByTestId(id);
    await button.focus();
    await page.keyboard.down("Space");
    try {
      await expect
        .poll(() =>
          button.evaluate((element) => {
            const computed = getComputedStyle(element);
            return { backgroundColor: computed.backgroundColor, color: computed.color };
          }),
        )
        .toEqual({
          backgroundColor: "rgba(0, 0, 0, 0)",
          color: "rgb(24, 76, 96)",
        });
    } finally {
      await page.keyboard.up("Space");
    }
  }
});

test("doctor link with an icon and count keeps 20px height, 4px gaps, and six-pixel count padding", async ({
  page,
}) => {
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  const button = page.getByTestId("link-image-count");
  const geometry = await button.evaluate((element) => {
    const style = getComputedStyle(element);
    const count = element.querySelector<HTMLElement>(".ui-button__count");
    if (!count) throw new Error("Button count is missing");
    const bounds = element.getBoundingClientRect();
    const countStyle = getComputedStyle(count);
    return {
      height: bounds.height,
      gap: style.gap,
      countPaddingLeft: countStyle.paddingLeft,
      countPaddingRight: countStyle.paddingRight,
      countHeight: count.getBoundingClientRect().height,
    };
  });

  expect(geometry).toEqual({
    height: 20,
    gap: "4px",
    countPaddingLeft: "6px",
    countPaddingRight: "6px",
    countHeight: 18,
  });
});

test("doctor field hints use muted ink and patient hints keep subtle ink", async ({ page }) => {
  for (const mode of [
    { forma: "vrach", tsvet: "vrach", expected: "rgb(77, 91, 101)" },
    {
      forma: "patsient",
      tsvet: "patsient-prokhladnaya",
      expected: "rgb(91, 105, 115)",
    },
  ]) {
    await page.goto(`/?case=visual-review&forma=${mode.forma}&tsvet=${mode.tsvet}`);
    await expect(page.getByText("Подсказка под адресом", { exact: true })).toHaveCSS(
      "color",
      mode.expected,
    );
  }
});
