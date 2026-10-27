import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";

const EVIDENCE_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../.superpowers/sdd/2026-10-21-20-shtil-design-system-design/evidence/components",
);

test("password visibility preserves the value and caret without submitting the form", async ({
  page,
}) => {
  await page.goto("/?case=password");
  const password = page.getByRole("textbox", { name: "Пароль" });
  await password.click();
  await password.press("End");
  await password.press("ArrowLeft");
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(8);

  const showPassword = page.getByRole("button", { name: "Показать пароль" });
  await showPassword.click();

  await expect(password).toHaveAttribute("type", "text");
  await expect(password).toHaveValue("secret123");
  await expect(password).toBeFocused();
  await waitForTwoFrames(page);
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(8);
  await expect(page.getByTestId("submit-count")).toHaveText("0");

  await page.getByRole("button", { name: "Скрыть пароль" }).click();
  await expect(password).toHaveAttribute("type", "password");
  await expect(password).toHaveValue("secret123");
  await expect(password).toBeFocused();
  await waitForTwoFrames(page);
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(8);
  await expect(page.getByTestId("submit-count")).toHaveText("0");

  const visibilityToggle = page.locator(".ui-field__password-toggle");
  await visibilityToggle.focus();
  await page.keyboard.press("Enter");
  await expect(password).toHaveAttribute("type", "text");
  await expect(visibilityToggle).toBeFocused();
  await waitForTwoFrames(page);
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(8);
  await expect(page.getByTestId("submit-count")).toHaveText("0");
});

test("password visibility does not restore a stale caret after the user types", async ({
  page,
}) => {
  await page.goto("/?case=password");
  const password = page.getByRole("textbox", { name: "Пароль" });
  await password.fill("ab");
  await password.evaluate((field: HTMLInputElement) => field.setSelectionRange(1, 1));
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(1);

  await deferAnimationFrames(page);
  await page.evaluate(() => {
    const toggle = document.querySelector<HTMLButtonElement>(".ui-field__password-toggle");
    if (!toggle) throw new Error("Password visibility button is missing");
    toggle.click();
  });
  await expect(password).toHaveAttribute("type", "text");
  await password.press("End");
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(2);
  await flushDeferredAnimationFrames(page);
  expect(await password.evaluate((field: HTMLInputElement) => field.selectionStart)).toBe(2);

  await page.goto("/?case=password");
  await password.fill("ab");
  await password.evaluate((field: HTMLInputElement) => field.setSelectionRange(1, 1));
  await deferAnimationFrames(page);
  await page.evaluate(() => {
    const toggle = document.querySelector<HTMLButtonElement>(".ui-field__password-toggle");
    if (!toggle) throw new Error("Password visibility button is missing");
    toggle.click();
  });
  await expect(password).toHaveAttribute("type", "text");
  await password.evaluate((field: HTMLInputElement) => field.setSelectionRange(1, 1));
  await password.press("x");
  await expect(password).toHaveValue("axb");

  await flushDeferredAnimationFrames(page);
  await password.press("y");
  await expect(password).toHaveValue("axyb");
});

test("password visibility does not steal focus from a newer target", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?forma=vrach&tsvet=vrach&nastroenie=teplyy-khorosho");
  const password = page.getByRole("textbox", { name: "Пароль" });
  const toggle = page.locator(".ui-field__password-toggle");
  const nextTarget = page.getByRole("button", { name: "Основная" });

  await password.focus();
  await password.press("End");
  const toggleBounds = await toggle.boundingBox();
  const targetBounds = await nextTarget.boundingBox();
  expect(toggleBounds).not.toBeNull();
  expect(targetBounds).not.toBeNull();
  if (!toggleBounds || !targetBounds) throw new Error("Password controls are unavailable");

  await deferAnimationFrames(page);
  await page.mouse.click(
    toggleBounds.x + toggleBounds.width / 2,
    toggleBounds.y + toggleBounds.height / 2,
  );
  await expect(password).toHaveAttribute("type", "text");
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(password).toBeFocused();

  await page.mouse.click(
    targetBounds.x + targetBounds.width / 2,
    targetBounds.y + targetBounds.height / 2,
  );
  await expect(nextTarget).toBeFocused();
  await flushDeferredAnimationFrames(page);
  await expect(nextTarget).toBeFocused();
});

async function deferAnimationFrames(page: Page) {
  await page.evaluate(() => {
    let nextFrameId = 1;
    const callbacks = new Map<number, FrameRequestCallback>();
    const controlledWindow = window as Window & {
      __flushControlledFrames?: () => void;
    };
    controlledWindow.requestAnimationFrame = (callback) => {
      const frameId = nextFrameId;
      nextFrameId += 1;
      callbacks.set(frameId, callback);
      return frameId;
    };
    controlledWindow.cancelAnimationFrame = (frameId) => {
      callbacks.delete(frameId);
    };
    controlledWindow.__flushControlledFrames = () => {
      const pending = [...callbacks.values()];
      callbacks.clear();
      for (const callback of pending) callback(performance.now());
    };
  });
}

async function flushDeferredAnimationFrames(page: Page) {
  await page.evaluate(() => {
    const controlledWindow = window as Window & { __flushControlledFrames?: () => void };
    controlledWindow.__flushControlledFrames?.();
  });
}

async function waitForTwoFrames(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

test("a loading button is disabled and cannot submit repeatedly", async ({ page }) => {
  await page.goto("/?case=button");
  const save = page.getByRole("button", { name: "Сохранить" });

  await expect(save).toBeDisabled();
  await expect(save).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "Отключено" })).toBeDisabled();
  await expect(page.getByTestId("submit-count")).toHaveText("0");
});

test("segmented control exposes one tab stop and updates its controlled value by keyboard", async ({
  page,
}) => {
  await page.goto("/?case=segments");
  const group = page.getByRole("radiogroup", { name: "Период" });
  const week = page.getByRole("radio", { name: "Неделя" });
  const month = page.getByRole("radio", { name: "Месяц" });
  const all = page.getByRole("radio", { name: "Всё время" });

  await expect(week).toHaveJSProperty("tabIndex", 0);
  await expect(month).toHaveJSProperty("tabIndex", -1);
  await week.focus();
  await page.keyboard.press("ArrowRight");
  await expect(month).toBeFocused();
  await expect(month).toBeChecked();
  await expect(page.getByTestId("selected-segment")).toHaveText("month");

  await page.keyboard.press("End");
  await expect(all).toBeFocused();
  await expect(page.getByTestId("selected-segment")).toHaveText("all");
  await page.keyboard.press("Home");
  await expect(week).toBeFocused();
  await expect(page.getByTestId("selected-segment")).toHaveText("week");
  await expect(group.getByRole("radio")).toHaveCount(4);
});

test("popover opens by keyboard, stays in the viewport, and restores focus on Escape", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 320 });
  await page.goto("/?case=popover");
  const trigger = page.getByRole("button", { name: "Подробности" });
  await trigger.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog", { name: "Пояснение" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "Действие внутри" })).toBeFocused();
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await dialog.screenshot({ path: resolve(EVIDENCE_DIR, "popover-open-360x320.png") });
  const bounds = await dialog.boundingBox();
  expect(bounds).not.toBeNull();
  if (!bounds) throw new Error("Popover geometry is unavailable");
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(360);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(320);

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("popover closes on outside click without taking focus from the clicked control", async ({
  page,
}) => {
  await page.goto("/?case=popover");
  await page.getByRole("button", { name: "Подробности" }).click();
  await expect(page.getByRole("dialog", { name: "Пояснение" })).toBeVisible();

  const outside = page.getByTestId("outside");
  await outside.click();
  await expect(page.getByRole("dialog", { name: "Пояснение" })).toBeHidden();
  await expect(outside).toBeFocused();
});

test("native disabled buttons cannot be activated", async ({ page }) => {
  await page.goto("/?case=button");
  const disabled = page.getByRole("button", { name: "Отключено" });
  await expect(disabled).toBeDisabled();
  await expect(page.getByTestId("submit-count")).toHaveText("0");
});

test("keyboard Space shows the pressed appearance without pointer hover", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?forma=vrach&tsvet=vrach&nastroenie=teplyy-khorosho");
  await page.mouse.move(1270, 820);

  for (const variant of [
    { name: "Основная", token: "--color-primary-hover" },
    { name: "Обычная", token: "--color-surface-sunken" },
  ]) {
    const button = page.getByRole("button", { name: variant.name });
    await button.focus();
    expect(await button.evaluate((element) => element.matches(":hover"))).toBe(false);

    await page.keyboard.down("Space");
    const pressed = await button.evaluate((element, token) => {
      const probe = document.createElement("span");
      probe.style.backgroundColor = `var(${token})`;
      document.body.append(probe);
      const expectedBackground = getComputedStyle(probe).backgroundColor;
      probe.remove();

      return {
        active: element.matches(":active"),
        expectedBackground,
      };
    }, variant.token);
    expect(pressed.active).toBe(true);
    await expect
      .poll(() => button.evaluate((element) => getComputedStyle(element).backgroundColor))
      .toBe(pressed.expectedBackground);
    await page.keyboard.up("Space");
  }
});

test("theme modes load typography and save component screenshots for review", async ({ page }) => {
  test.setTimeout(120_000);
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  const formas = ["vrach", "patsient"] as const;
  const tsvety = ["vrach", "patsient-prokhladnaya", "patsient-teplaya"] as const;
  const nastroeniya = ["teplyy-khorosho", "zelenyy-khorosho", "spokoynyy"] as const;
  const viewports = [
    { width: 360, height: 780 },
    { width: 1280, height: 832 },
  ];
  const showcaseRegions = [
    "Кнопки",
    "Поля",
    "Статусы и счётчики",
    "Сегменты и аватары",
    "Знак и бренд",
    "Поповер",
  ];
  const moodColors = new Set<string>();

  for (const forma of formas) {
    for (const tsvet of tsvety) {
      for (const nastroenie of nastroeniya) {
        const mode = `${forma}-${tsvet}-${nastroenie}`;
        const query = new URLSearchParams({ forma, tsvet, nastroenie });

        for (const viewport of viewports) {
          await page.setViewportSize(viewport);
          await page.goto(`/?${query.toString()}`);
          await page.evaluate(() => document.fonts.ready.then(() => true));

          const attributes = await page.locator("html").evaluate((element) => ({
            forma: element.getAttribute("data-forma"),
            tsvet: element.getAttribute("data-tsvet"),
            nastroenie: element.getAttribute("data-nastroenie"),
          }));
          expect(attributes).toEqual({ forma, tsvet, nastroenie });

          const typography = await page
            .getByRole("button", { name: "Основная" })
            .evaluate((button) => {
              const field = document.querySelector(".ui-field__input");
              const control = document.querySelector(".ui-field__control");
              const quietButton = document.querySelector<HTMLButtonElement>(
                '.ui-button[data-variant="quiet"]',
              );
              if (!field || !control || !quietButton) {
                throw new Error("Fixture fields or quiet button are missing");
              }

              const style = getComputedStyle(button);
              const quietStyle = getComputedStyle(quietButton);
              return {
                fontFamily: style.fontFamily,
                fontSize: style.fontSize,
                buttonHeight: style.height,
                quietHeight: quietStyle.height,
                quietPadding: [
                  quietStyle.paddingTop,
                  quietStyle.paddingRight,
                  quietStyle.paddingBottom,
                  quietStyle.paddingLeft,
                ],
                fieldFontSize: getComputedStyle(field).fontSize,
                fieldHeight: getComputedStyle(control).height,
                moodColor: getComputedStyle(document.documentElement)
                  .getPropertyValue("--mood-3")
                  .trim(),
                hasOnest: document.fonts.check("500 15px Onest"),
              };
            });
          expect(typography.fontFamily).toContain("Onest");
          expect(typography.fontSize).toBe(forma === "patsient" ? "17px" : "15px");
          expect(typography.buttonHeight).toBe(forma === "patsient" ? "56px" : "36px");
          expect(typography.quietHeight).toBe(forma === "patsient" ? "56px" : "36px");
          expect(typography.quietPadding).toEqual(
            forma === "patsient" ? ["16px", "24px", "16px", "24px"] : ["0px", "0px", "0px", "0px"],
          );
          expect(typography.fieldFontSize).toBe(forma === "patsient" ? "17px" : "15px");
          expect(typography.fieldHeight).toBe(forma === "patsient" ? "58px" : "40px");
          expect(typography.hasOnest).toBe(true);
          if (forma === "vrach" && tsvet === "vrach") moodColors.add(typography.moodColor);

          const suffix = `${mode}-${viewport.width}`;
          await page.screenshot({ path: resolve(EVIDENCE_DIR, `${suffix}.png`), fullPage: true });

          if (
            (forma === "vrach" && tsvet === "vrach" && nastroenie === "teplyy-khorosho") ||
            (forma === "patsient" &&
              tsvet === "patsient-prokhladnaya" &&
              nastroenie === "zelenyy-khorosho") ||
            (forma === "patsient" && tsvet === "patsient-teplaya" && nastroenie === "spokoynyy")
          ) {
            for (const region of showcaseRegions) {
              await page
                .getByRole("region", { name: region })
                .screenshot({ path: resolve(EVIDENCE_DIR, `${suffix}-${regionSlug(region)}.png`) });
            }
          }
        }
      }
    }
  }

  expect(moodColors.size).toBe(3);
});

function regionSlug(label: string) {
  return label.toLocaleLowerCase("ru-RU").replaceAll(" ", "-");
}
