import { expect, test } from "@playwright/test";

test("long popover at the top edge stays below its trigger", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=scroll-popover&forma=vrach&tsvet=vrach");
  await expectLongPopover(
    page,
    "Длинный поповер у верхнего края",
    "Длинная подсказка у верхнего края",
    "bottom",
  );
});

test("long popover at the bottom edge stays above its trigger", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=scroll-popover&forma=vrach&tsvet=vrach");
  await expectLongPopover(
    page,
    "Длинный поповер у нижнего края",
    "Длинная подсказка у нижнего края",
    "top",
  );
});

async function expectLongPopover(
  page: import("@playwright/test").Page,
  triggerName: string,
  dialogName: string,
  expectedSide: "top" | "bottom",
) {
  await page.getByRole("button", { name: triggerName, exact: true }).click();
  const dialog = page.getByRole("dialog", { name: dialogName, exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute("data-side", expectedSide);

  const geometry = await dialog.evaluate((panel) => {
    const trigger = panel
      .closest<HTMLElement>(".ui-popover")
      ?.querySelector<HTMLElement>(".ui-popover__trigger");
    const arrow = panel.querySelector<SVGSVGElement>(".ui-popover__arrow");
    const content = panel.querySelector<HTMLElement>(".ui-popover__content");
    if (!trigger || !arrow || !content) throw new Error("Popover geometry is incomplete");

    const panelBounds = panel.getBoundingClientRect();
    const triggerBounds = trigger.getBoundingClientRect();
    const arrowBounds = arrow.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const side = panel.dataset.side;
    const availableHeight =
      side === "bottom"
        ? viewportHeight - triggerBounds.bottom - 8 - 10
        : triggerBounds.top - 8 - 10;

    return {
      side,
      panel: {
        top: panelBounds.top,
        right: panelBounds.right,
        bottom: panelBounds.bottom,
        left: panelBounds.left,
        height: panelBounds.height,
      },
      trigger: {
        top: triggerBounds.top,
        right: triggerBounds.right,
        bottom: triggerBounds.bottom,
        left: triggerBounds.left,
      },
      arrow: {
        top: arrowBounds.top,
        bottom: arrowBounds.bottom,
      },
      availableHeight,
      viewportHeight,
      scrollable: content.scrollHeight > content.clientHeight,
      contentOverflowY: getComputedStyle(content).overflowY,
    };
  });

  expect(geometry.panel.left).toBeGreaterThanOrEqual(8);
  expect(geometry.panel.right).toBeLessThanOrEqual(1272);
  expect(geometry.panel.top).toBeGreaterThanOrEqual(8);
  expect(geometry.panel.bottom).toBeLessThanOrEqual(824);
  expect(geometry.panel.height).toBeLessThanOrEqual(geometry.availableHeight + 1);
  expect(geometry.scrollable).toBe(true);
  expect(geometry.contentOverflowY).toBe("auto");

  if (expectedSide === "bottom") {
    expect(geometry.panel.top).toBeGreaterThanOrEqual(geometry.trigger.bottom);
    expect(geometry.panel.top - geometry.trigger.bottom).toBeLessThanOrEqual(10);
    expect(Math.abs(geometry.arrow.bottom - geometry.panel.top)).toBeLessThanOrEqual(1);
    expect(geometry.arrow.top).toBeGreaterThanOrEqual(geometry.trigger.bottom);
    expect(geometry.arrow.top - geometry.trigger.bottom).toBeLessThanOrEqual(10);
  } else {
    expect(geometry.panel.bottom).toBeLessThanOrEqual(geometry.trigger.top);
    expect(geometry.trigger.top - geometry.panel.bottom).toBeLessThanOrEqual(10);
    expect(Math.abs(geometry.arrow.top - geometry.panel.bottom)).toBeLessThanOrEqual(1);
    expect(geometry.arrow.bottom).toBeLessThanOrEqual(geometry.trigger.top);
    expect(geometry.trigger.top - geometry.arrow.bottom).toBeLessThanOrEqual(10);
  }
}
