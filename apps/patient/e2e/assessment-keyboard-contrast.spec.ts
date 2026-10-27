import { expect, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";
import { SCALE_ID } from "./assessment-fixtures";
import { contrastRatio } from "./contrast";

test("form radios work by keyboard with visible focus and readable text at 360px", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto(`/patient/assessments/${SCALE_ID}`);

  const group = page.getByRole("group", { name: "I felt calm." });
  const options = group.getByRole("radio");
  await options.first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(options.nth(1)).toBeFocused();
  await expect(options.nth(1)).toBeChecked();

  const appearance = await page.evaluate(() => {
    const legend = document.querySelector("fieldset legend");
    const panel = legend?.closest("section");
    const mutedText = document.querySelector('form > p[aria-live="polite"]');
    const submit = document.querySelector('form button[type="submit"]');
    const focused = document.activeElement;
    const focusedLabel = focused?.closest("label");
    const focusStyle = focusedLabel ? getComputedStyle(focusedLabel) : null;
    if (!legend || !panel || !mutedText || !submit || !focusStyle) return null;
    return {
      primaryText: getComputedStyle(legend).color,
      secondaryText: getComputedStyle(mutedText).color,
      panelBackground: getComputedStyle(panel).backgroundColor,
      buttonText: getComputedStyle(submit).color,
      buttonBackground: getComputedStyle(submit).backgroundColor,
      outlineStyle: focusStyle.outlineStyle,
      outlineWidth: focusStyle.outlineWidth,
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  expect(appearance).not.toBeNull();
  if (!appearance) throw new Error("Could not inspect the focused form control");
  expect(contrastRatio(appearance.primaryText, appearance.panelBackground)).toBeGreaterThanOrEqual(
    4.5,
  );
  expect(
    contrastRatio(appearance.secondaryText, appearance.panelBackground),
  ).toBeGreaterThanOrEqual(4.5);
  expect(contrastRatio(appearance.buttonText, appearance.buttonBackground)).toBeGreaterThanOrEqual(
    4.5,
  );
  expect(appearance.outlineStyle).not.toBe("none");
  expect(Number.parseFloat(appearance.outlineWidth)).toBeGreaterThan(0);
  expect(appearance.scrollWidth).toBeLessThanOrEqual(appearance.viewportWidth);
});
