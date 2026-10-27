import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Locator, test } from "@playwright/test";

const EVIDENCE_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../.superpowers/sdd/2026-10-21-20-shtil-design-system-design/evidence/review-runtime",
);

test("doctor field labels use v4 typography and eight-pixel spacing", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?forma=vrach&tsvet=vrach&nastroenie=spokoynyy");

  const styles = await Promise.all(
    ["Электронная почта", "Причина"].map((label) =>
      page.getByRole("textbox", { name: label, exact: true }).evaluate((input, expectedLabel) => {
        const field = input.closest<HTMLElement>(".ui-field");
        const fieldLabel = field?.querySelector<HTMLElement>(".ui-field__label");
        if (!field || !fieldLabel) {
          throw new Error(`Field markup for ${expectedLabel} is missing`);
        }
        const labelStyle = getComputedStyle(fieldLabel);
        return {
          fontSize: labelStyle.fontSize,
          lineHeight: labelStyle.lineHeight,
          fontWeight: labelStyle.fontWeight,
          letterSpacing:
            labelStyle.letterSpacing === "normal" ? 0 : Number.parseFloat(labelStyle.letterSpacing),
          color: labelStyle.color,
          gap: getComputedStyle(field).rowGap,
        };
      }, label),
    ),
  );

  expect(styles).toEqual([
    {
      fontSize: "14px",
      lineHeight: "20px",
      fontWeight: "400",
      letterSpacing: 0,
      color: "rgb(21, 29, 35)",
      gap: "8px",
    },
    {
      fontSize: "14px",
      lineHeight: "20px",
      fontWeight: "400",
      letterSpacing: 0,
      color: "rgb(21, 29, 35)",
      gap: "8px",
    },
  ]);
});

test("popover scrolls its content while keeping the 16-by-8 tail visible on either side", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=scroll-popover&forma=vrach&tsvet=vrach");
  await page.evaluate(() => document.fonts.ready.then(() => true));
  mkdirSync(EVIDENCE_DIR, { recursive: true });

  const belowTrigger = page.getByRole("button", { name: "Короткий поповер снизу" });
  await belowTrigger.click();
  const belowDialog = page.getByRole("dialog", { name: "Короткая подсказка снизу" });
  await expect(belowDialog).toHaveAttribute("data-side", "bottom");
  await expectVisibleTail(belowDialog, "bottom", false);
  await expect
    .poll(() => belowDialog.evaluate((dialog) => getComputedStyle(dialog).opacity))
    .toBe("1");
  await page.screenshot({
    path: resolve(EVIDENCE_DIR, "popover-bottom-full-1280.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");

  const aboveTrigger = page.getByRole("button", { name: "Короткий поповер сверху" });
  await aboveTrigger.click();
  const aboveDialog = page.getByRole("dialog", { name: "Короткая подсказка сверху" });
  await expect(aboveDialog).toHaveAttribute("data-side", "top");
  await expectVisibleTail(aboveDialog, "top", false);
  await expect
    .poll(() => aboveDialog.evaluate((dialog) => getComputedStyle(dialog).opacity))
    .toBe("1");
  await page.screenshot({
    path: resolve(EVIDENCE_DIR, "popover-top-full-1280.png"),
    fullPage: true,
  });
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Длинный поповер", exact: true }).click();
  const longDialog = page.getByRole("dialog", { name: "Прокручиваемая подсказка", exact: true });
  await expect(longDialog).toBeVisible();
  await expectVisibleTail(longDialog, "bottom", true);
  await expect
    .poll(() => longDialog.evaluate((dialog) => getComputedStyle(dialog).opacity))
    .toBe("1");
  await page.screenshot({
    path: resolve(EVIDENCE_DIR, "popover-scroll-full-1280.png"),
    fullPage: true,
  });
});

test("fields keep their geometry when focused or invalid in doctor and patient modes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  const mismatches: Array<{
    forma: string;
    label: string;
    before: number;
    focused: number;
  }> = [];
  const errorPairMismatches: Array<{
    forma: string;
    pair: string;
    regular: number;
    invalid: number;
  }> = [];

  for (const mode of [
    { forma: "vrach", tsvet: "vrach" },
    { forma: "patsient", tsvet: "patsient-prokhladnaya" },
  ]) {
    await page.goto(`/?forma=${mode.forma}&tsvet=${mode.tsvet}`);
    const state = await page.evaluate(() => {
      const labels = ["Электронная почта", "Пароль", "Причина", "Ошибка", "Причина с ошибкой"];
      return labels.map((label) => {
        const field = [...document.querySelectorAll<HTMLElement>(".ui-field")].find(
          (candidate) => candidate.querySelector(".ui-field__label")?.textContent === label,
        );
        const control = field?.querySelector<HTMLElement>(".ui-field__control");
        const input = field?.querySelector<HTMLElement>("input, textarea");
        if (!field || !control || !input) throw new Error(`Field control for ${label} is missing`);
        const before = control.getBoundingClientRect().height;
        input.focus();
        const focused = control.getBoundingClientRect().height;
        input.blur();
        return { label, before, focused };
      });
    });
    for (const field of state) {
      if (field.before !== field.focused) {
        mismatches.push({ forma: mode.forma, ...field });
      }
    }
    const heights = new Map(state.map(({ label, before }) => [label, before]));
    for (const [regularLabel, invalidLabel, pair] of [
      ["Электронная почта", "Ошибка", "single-line"],
      ["Причина", "Причина с ошибкой", "multiline"],
    ] as const) {
      const regular = heights.get(regularLabel);
      const invalid = heights.get(invalidLabel);
      if (regular === undefined || invalid === undefined) {
        throw new Error(`Field heights for ${pair} are unavailable`);
      }
      if (regular !== invalid)
        errorPairMismatches.push({ forma: mode.forma, pair, regular, invalid });
    }
  }

  expect(mismatches).toEqual([]);
  expect(errorPairMismatches).toEqual([]);
});

test("24-pixel doctor avatars use the reference label typography", async ({ page }) => {
  await page.goto("/?forma=vrach&tsvet=vrach");
  const style = await page.locator('.ui-avatar[data-size="24"]').evaluate((avatar) => {
    const computed = getComputedStyle(avatar);
    const bounds = avatar.getBoundingClientRect();
    return {
      width: bounds.width,
      height: bounds.height,
      fontSize: computed.fontSize,
      lineHeight: computed.lineHeight,
      fontWeight: computed.fontWeight,
      letterSpacing: computed.letterSpacing,
    };
  });

  expect(style).toEqual({
    width: 24,
    height: 24,
    fontSize: "12px",
    lineHeight: "16px",
    fontWeight: "500",
    letterSpacing: "0.2px",
  });
});

test("patient password eye has a 24-pixel icon and a 44-pixel target inset six pixels", async ({
  page,
}) => {
  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/?forma=patsient&tsvet=patsient-prokhladnaya");

  const geometry = await page.locator(".ui-field__control--password").evaluate((control) => {
    const toggle = control.querySelector<HTMLButtonElement>(".ui-field__password-toggle");
    const icon = toggle?.querySelector("img");
    if (!toggle || !icon) throw new Error("Password eye control is missing");
    const controlBounds = control.getBoundingClientRect();
    const toggleBounds = toggle.getBoundingClientRect();
    const iconBounds = icon.getBoundingClientRect();
    return {
      targetWidth: toggleBounds.width,
      targetHeight: toggleBounds.height,
      iconWidth: iconBounds.width,
      iconHeight: iconBounds.height,
      rightInset: controlBounds.right - toggleBounds.right,
    };
  });

  expect(geometry).toEqual({
    targetWidth: 44,
    targetHeight: 44,
    iconWidth: 24,
    iconHeight: 24,
    rightInset: 6,
  });
});

test("doctor segments show their full labels at desktop width and stay within mobile width", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?forma=vrach&tsvet=vrach");

  const labels = await page
    .getByRole("radiogroup", { name: "Фильтр списка" })
    .locator(".ui-segmented__label")
    .evaluateAll((elements) =>
      elements.map((element) => ({
        label: element.textContent,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      })),
    );
  const desktopBounds = await page.getByRole("radiogroup", { name: "Фильтр списка" }).boundingBox();
  expect(desktopBounds).not.toBeNull();
  if (!desktopBounds) throw new Error("Desktop segmented control geometry is unavailable");
  expect(desktopBounds.width).toBe(328);
  expect(labels.map(({ label }) => label)).toEqual(["Все", "С сигналами", "Приглашённые"]);
  expect(labels.every(({ clientWidth, scrollWidth }) => scrollWidth <= clientWidth)).toBe(true);

  await page.setViewportSize({ width: 360, height: 832 });
  const groupBounds = await page.getByRole("radiogroup", { name: "Фильтр списка" }).boundingBox();
  expect(groupBounds).not.toBeNull();
  if (!groupBounds) throw new Error("Segmented control geometry is unavailable");
  expect(groupBounds.x + groupBounds.width).toBeLessThanOrEqual(360);
  expect(await page.locator("body").evaluate((body) => body.scrollWidth)).toBeLessThanOrEqual(360);
});

test("doctor links match their reference typography", async ({ page }) => {
  await page.goto("/?forma=vrach&tsvet=vrach");
  const linkStyle = await page.getByRole("button", { name: "Ссылка" }).evaluate((button) => {
    const style = getComputedStyle(button);
    return { fontSize: style.fontSize, lineHeight: style.lineHeight, fontWeight: style.fontWeight };
  });
  expect(linkStyle).toEqual({ fontSize: "14px", lineHeight: "20px", fontWeight: "600" });

  // Восстановления пароля нет: «Забыли пароль?» в форме входа не показывается (кадр 01в).
  await page.goto("/?case=login&forma=vrach&tsvet=vrach");
  await expect(page.getByRole("button", { name: "Забыли пароль?" })).toHaveCount(0);
});

test("primary button count follows the 18-pixel reference pill", async ({ page }) => {
  await page.goto("/?forma=vrach&tsvet=vrach");
  const button = page.getByRole("button", { name: "Непрочитанные сообщения" });
  const count = button.locator(".ui-button__count");
  const geometry = await count.evaluate((badge) => {
    const style = getComputedStyle(badge);
    const bounds = badge.getBoundingClientRect();
    return {
      width: bounds.width,
      height: bounds.height,
      background: style.backgroundColor,
      foreground: style.color,
      buttonForeground: badge.closest("button")
        ? getComputedStyle(badge.closest("button") as HTMLButtonElement).color
        : null,
    };
  });

  expect(geometry.width).toBeGreaterThanOrEqual(18);
  expect(geometry.height).toBe(18);
  expect(geometry.background).toBe(geometry.buttonForeground);
  expect(geometry.foreground).not.toBe(geometry.background);
});

async function expectVisibleTail(dialog: Locator, side: "top" | "bottom", needsScroll: boolean) {
  const geometry = await dialog.evaluate((panel) => {
    const arrow = panel.querySelector<SVGSVGElement>(".ui-popover__arrow");
    const scrollContent = panel.querySelector<HTMLElement>(".ui-popover__content") ?? panel;
    const panelBounds = panel.getBoundingClientRect();
    const pseudoStyle = arrow ? null : getComputedStyle(panel, "::before");
    const arrowBounds = arrow?.getBoundingClientRect();
    const arrowWidth = arrowBounds?.width ?? Number.parseFloat(pseudoStyle?.width ?? "0");
    const arrowHeight = arrowBounds?.height ?? Number.parseFloat(pseudoStyle?.height ?? "0");
    let arrowY = arrowBounds?.y ?? Number.NaN;
    if (!arrow && pseudoStyle) {
      const pseudoTop = Number.parseFloat(pseudoStyle.top);
      const pseudoBottom = Number.parseFloat(pseudoStyle.bottom);
      arrowY = Number.isFinite(pseudoTop)
        ? panelBounds.top + pseudoTop
        : panelBounds.bottom - pseudoBottom - arrowHeight;
    }
    const contentStyle = getComputedStyle(scrollContent);
    return {
      panelOverflowX: getComputedStyle(panel).overflowX,
      panelOverflowY: getComputedStyle(panel).overflowY,
      contentOverflowY: contentStyle.overflowY,
      scrollable: scrollContent.scrollHeight > scrollContent.clientHeight,
      arrow: { y: arrowY, width: arrowWidth, height: arrowHeight },
      panel: {
        top: panelBounds.top,
        bottom: panelBounds.bottom,
      },
    };
  });

  expect(geometry.panelOverflowX).toBe("visible");
  expect(geometry.panelOverflowY).toBe("visible");
  expect(geometry.contentOverflowY).toBe("auto");
  expect(geometry.scrollable).toBe(needsScroll);
  expect(geometry.arrow.width).toBe(16);
  expect(geometry.arrow.height).toBe(8);
  if (side === "bottom") {
    expect(geometry.arrow.y).toBeLessThan(geometry.panel.top);
    expect(
      Math.abs(geometry.arrow.y + geometry.arrow.height - geometry.panel.top),
    ).toBeLessThanOrEqual(1);
  } else {
    expect(Math.abs(geometry.arrow.y - geometry.panel.bottom)).toBeLessThanOrEqual(1);
    expect(geometry.arrow.y + geometry.arrow.height).toBeGreaterThan(geometry.panel.bottom);
  }
}
