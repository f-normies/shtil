import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Locator, type Page, test } from "@playwright/test";

const EVIDENCE_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../.superpowers/sdd/2026-10-21-20-shtil-design-system-design/evidence/visual-fixed",
);

async function saveReviewScreenshot(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready.then(() => true));
  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.screenshot({ path: resolve(EVIDENCE_DIR, name), fullPage: true });
}

async function screenshotAtIntegerBox(icon: Locator, backgroundColor?: string) {
  await icon.evaluate((element) => {
    const { width, height } = element.getBoundingClientRect();
    Object.assign(element.style, {
      position: "fixed",
      left: "32px",
      top: "32px",
      width: `${width}px`,
      height: `${height}px`,
      margin: "0",
    });
  });
  if (backgroundColor) {
    await icon.evaluate((element, color) => {
      element.style.backgroundColor = color;
    }, backgroundColor);
  }
  return icon.screenshot();
}

async function expectScreenshotPixelsEqual(
  page: Page,
  actual: Buffer,
  reference: Buffer,
  description = "SVG source and rendered pixels should match",
) {
  const comparison = await page.evaluate(
    async ({ actualPng, referencePng }) => {
      async function decode(png: string) {
        const image = new Image();
        image.src = `data:image/png;base64,${png}`;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas is unavailable");
        context.drawImage(image, 0, 0);
        return {
          width: canvas.width,
          height: canvas.height,
          pixels: context.getImageData(0, 0, canvas.width, canvas.height).data,
        };
      }

      const [actualImage, referenceImage] = await Promise.all([
        decode(actualPng),
        decode(referencePng),
      ]);
      if (
        actualImage.width !== referenceImage.width ||
        actualImage.height !== referenceImage.height
      ) {
        return {
          actualSize: [actualImage.width, actualImage.height],
          referenceSize: [referenceImage.width, referenceImage.height],
          differingPixels: null,
        };
      }

      let differingPixels = 0;
      for (let index = 0; index < actualImage.pixels.length; index += 4) {
        if (
          actualImage.pixels[index] !== referenceImage.pixels[index] ||
          actualImage.pixels[index + 1] !== referenceImage.pixels[index + 1] ||
          actualImage.pixels[index + 2] !== referenceImage.pixels[index + 2] ||
          actualImage.pixels[index + 3] !== referenceImage.pixels[index + 3]
        ) {
          differingPixels += 1;
        }
      }
      return {
        actualSize: [actualImage.width, actualImage.height],
        referenceSize: [referenceImage.width, referenceImage.height],
        differingPixels,
      };
    },
    { actualPng: actual.toString("base64"), referencePng: reference.toString("base64") },
  );

  expect(comparison, description).toEqual({
    actualSize: comparison.referenceSize,
    referenceSize: comparison.referenceSize,
    differingPixels: 0,
  });
}

async function expectRenderedIconColor(
  page: import("@playwright/test").Page,
  icon: import("@playwright/test").Locator,
  color: string,
  description: string,
) {
  const screenshot = await icon.screenshot();
  const matchingPixels = await page.evaluate(
    async ({ png, expected }) => {
      const image = new Image();
      image.src = `data:image/png;base64,${png}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Canvas is unavailable");
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const [red, green, blue] = expected.match(/\d+/g)?.map(Number) ?? [];
      let count = 0;
      for (let index = 0; index < pixels.length; index += 4) {
        const pixelRed = pixels[index] ?? -1;
        const pixelGreen = pixels[index + 1] ?? -1;
        const pixelBlue = pixels[index + 2] ?? -1;
        const alpha = pixels[index + 3] ?? 0;
        if (pixelRed === red && pixelGreen === green && pixelBlue === blue && alpha > 0) {
          count += 1;
        }
      }
      return count;
    },
    { png: screenshot.toString("base64"), expected: color },
  );

  expect(matchingPixels, `${description} should render pixels in ${color}`).toBeGreaterThan(0);
}

test("nested child state updates an SVG mask and disconnects on unmount", async ({ page }) => {
  await page.addInitScript(() => {
    const NativeMutationObserver = window.MutationObserver;
    const records: Array<{ disconnected: boolean }> = [];
    Object.defineProperty(window, "__dynamicIconObserverRecords", { value: records });
    window.MutationObserver = class extends NativeMutationObserver {
      private trackedRecord: { disconnected: boolean } | null = null;

      override observe(target: Node, options?: MutationObserverInit) {
        if (target instanceof Element && target.closest('[data-testid="dynamic-icon-button"]')) {
          this.trackedRecord = { disconnected: false };
          records.push(this.trackedRecord);
        }
        super.observe(target, options);
      }

      override disconnect() {
        if (this.trackedRecord) this.trackedRecord.disconnected = true;
        super.disconnect();
      }
    } as typeof MutationObserver;
  });
  await page.goto("/?case=dynamic-icon&forma=vrach&tsvet=vrach");

  const button = page.getByTestId("dynamic-icon-button");
  const slot = button.locator(".ui-button__icon");
  const source = page.getByTestId("dynamic-icon-image");
  const maskImage = () =>
    slot
      .locator(".ui-button__icon-image")
      .evaluate((element) => getComputedStyle(element).maskImage);
  const observerStatus = () =>
    page.evaluate(() => {
      const state = window as Window & {
        __dynamicIconObserverRecords?: Array<{ disconnected: boolean }>;
      };
      return state.__dynamicIconObserverRecords?.map(({ disconnected }) => disconnected) ?? [];
    });

  await expect(source).toHaveAttribute("src", /#circle$/);
  await expect.poll(maskImage).toContain("#circle");
  const initialMask = await maskImage();
  expect(initialMask).toContain("#circle");
  await page.getByTestId("dynamic-icon-square").click();
  await expect(source).toHaveAttribute("src", /#square$/);
  await expect.poll(maskImage).toContain("#square");

  await page.getByTestId("dynamic-icon-select-inline").click();
  await expect(slot.getByTestId("dynamic-icon-inline-svg")).toBeAttached();
  await expect(slot.locator(".ui-button__icon-image")).toHaveCount(0);
  await page.getByTestId("dynamic-icon-circle").click();
  await expect(source).toHaveAttribute("src", /#circle$/);
  await expect.poll(maskImage).toContain("#circle");

  await expect.poll(observerStatus).toEqual([false]);
  await page.getByTestId("dynamic-icon-unmount").click();
  await expect(button).toHaveCount(0);
  await expect.poll(observerStatus).toEqual([true]);
});

test("selected required status keeps the white octagon and colored exclamation", async ({
  page,
}) => {
  for (const mode of [
    { forma: "vrach", tsvet: "vrach" },
    { forma: "patsient", tsvet: "patsient-prokhladnaya" },
  ]) {
    await page.setViewportSize(
      mode.forma === "vrach" ? { width: 1280, height: 832 } : { width: 360, height: 780 },
    );
    await page.goto(`/?case=visual-review&forma=${mode.forma}&tsvet=${mode.tsvet}`);
    const icon = page.locator(
      '.ui-status-badge[data-status="required"][data-selected="true"] .ui-status-icon',
    );
    const backdrop = "rgb(31, 95, 119)";
    const rendered = await screenshotAtIntegerBox(icon, backdrop);

    await page.goto(
      `/?case=visual-source&source=required-selected&forma=${mode.forma}&tsvet=${mode.tsvet}`,
    );
    const reference = await screenshotAtIntegerBox(page.getByTestId("visual-source"), backdrop);
    await expectScreenshotPixelsEqual(
      page,
      rendered,
      reference,
      `${mode.forma} selected required icon should match source pixels on the selected fill`,
    );

    await page.goto(`/?case=visual-review&forma=${mode.forma}&tsvet=${mode.tsvet}`);
    await saveReviewScreenshot(page, `${mode.forma}-required-selected.png`);
  }
});

test("buttons size and color image and inline SVG icons by mode and variant", async ({ page }) => {
  for (const mode of [
    {
      forma: "vrach",
      tsvet: "vrach",
      buttonHeight: 36,
      iconSize: 20,
      primaryColor: "rgb(255, 255, 255)",
      quietColor: "rgb(31, 95, 119)",
    },
    {
      forma: "patsient",
      tsvet: "patsient-prokhladnaya",
      buttonHeight: 56,
      iconSize: 24,
      primaryColor: "rgb(255, 255, 255)",
    },
  ]) {
    await page.setViewportSize(
      mode.forma === "vrach" ? { width: 1280, height: 832 } : { width: 360, height: 780 },
    );
    await page.goto(`/?case=visual-review&forma=${mode.forma}&tsvet=${mode.tsvet}`);
    const buttonIds =
      mode.forma === "vrach"
        ? ["primary-image", "primary-svg", "quiet-image", "quiet-svg", "primary-mixed"]
        : ["primary-image", "primary-svg"];
    const actual = await Promise.all(
      buttonIds.map((id) =>
        page.getByTestId(id).evaluate((button) => {
          const icons = Array.from(
            button.querySelectorAll<HTMLElement>(".ui-button__icon, .ui-button__end-icon"),
          );
          if (icons.length === 0) throw new Error("Button icon is missing");
          const buttonBounds = button.getBoundingClientRect();
          return {
            buttonHeight: buttonBounds.height,
            icons: icons.map((icon) => {
              const child =
                icon.querySelector<HTMLElement>(".ui-button__icon-image") ??
                icon.querySelector<HTMLElement>("svg, img");
              if (!child) throw new Error("Button icon has no rendered glyph");
              const wrapperBounds = icon.getBoundingClientRect();
              const childBounds = child.getBoundingClientRect();
              return {
                wrapper: { width: wrapperBounds.width, height: wrapperBounds.height },
                child: { width: childBounds.width, height: childBounds.height },
              };
            }),
            buttonColor: getComputedStyle(button).color,
          };
        }),
      ),
    );

    for (let index = 0; index < actual.length; index += 1) {
      const entry = actual[index];
      const buttonId = buttonIds[index];
      if (!entry || !buttonId) throw new Error("A button fixture is missing");
      expect(entry.buttonHeight).toBe(mode.buttonHeight);
      for (const icon of entry.icons) {
        expect(icon.wrapper).toEqual({ width: mode.iconSize, height: mode.iconSize });
        expect(icon.child).toEqual({ width: mode.iconSize, height: mode.iconSize });
      }
      const expectedColor =
        mode.forma === "vrach" && (index === 2 || index === 3)
          ? mode.quietColor
          : mode.primaryColor;
      if (!expectedColor) throw new Error("A mode is missing its button icon color");
      for (const selector of [".ui-button__icon", ".ui-button__end-icon"]) {
        const icon = page.getByTestId(buttonId).locator(selector);
        if (await icon.count()) {
          await expectRenderedIconColor(
            page,
            icon,
            expectedColor,
            `${mode.forma} ${buttonId} ${selector}`,
          );
        }
      }
      expect(entry.buttonColor).toBe(expectedColor);
    }
    await saveReviewScreenshot(page, `${mode.forma}-button-icons.png`);
  }
});

test("password eyes match the doctor and patient source glyphs in both doctor states", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 832 });
  await page.goto("/?case=password&forma=vrach&tsvet=vrach");
  const doctorEye = page.locator(".ui-field__password-toggle img");
  const doctorNormal = await screenshotAtIntegerBox(doctorEye, "white");

  await page.goto("/?case=visual-source&source=doctor-eye-normal&forma=vrach&tsvet=vrach");
  await expectScreenshotPixelsEqual(
    page,
    doctorNormal,
    await screenshotAtIntegerBox(page.getByTestId("visual-source"), "white"),
  );

  await page.goto("/?case=password&forma=vrach&tsvet=vrach");
  await page.getByRole("button", { name: "Показать пароль" }).click();
  const doctorVisible = await screenshotAtIntegerBox(
    page.locator(".ui-field__password-toggle img"),
    "white",
  );
  await page.goto("/?case=visual-source&source=doctor-eye-visible&forma=vrach&tsvet=vrach");
  await expectScreenshotPixelsEqual(
    page,
    doctorVisible,
    await screenshotAtIntegerBox(page.getByTestId("visual-source"), "white"),
  );

  await page.setViewportSize({ width: 360, height: 780 });
  await page.goto("/?case=password&forma=patsient&tsvet=patsient-prokhladnaya");
  const patientNormal = await screenshotAtIntegerBox(
    page.locator(".ui-field__password-toggle img"),
    "white",
  );
  await page.goto(
    "/?case=visual-source&source=patient-eye-normal&forma=patsient&tsvet=patsient-prokhladnaya",
  );
  await expectScreenshotPixelsEqual(
    page,
    patientNormal,
    await screenshotAtIntegerBox(page.getByTestId("visual-source"), "white"),
  );
});

test("proposal status icon matches the eight-arc Figma instance", async ({ page }) => {
  await page.goto("/?case=visual-review&forma=vrach&tsvet=vrach");
  const rendered = await screenshotAtIntegerBox(
    page.locator('.ui-status-icon[data-status="proposed"]'),
  );
  await page.goto("/?case=visual-source&source=proposed&forma=vrach&tsvet=vrach");
  await expectScreenshotPixelsEqual(
    page,
    rendered,
    await screenshotAtIntegerBox(page.getByTestId("visual-source")),
  );
});
