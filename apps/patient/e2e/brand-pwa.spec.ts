import { themeColors } from "@compass/ui/tokens";
import { expect, type Page, test } from "@playwright/test";
import { mockAssessmentApi } from "./assessment-api-mock";

type ManifestIcon = {
  src: string;
  sizes: string;
  purpose?: string;
};

type WebAppManifest = {
  name: string;
  short_name: string;
  description: string;
  theme_color: string;
  background_color: string;
  icons: ManifestIcon[];
};

async function readManifest(page: Page): Promise<WebAppManifest | null> {
  return page.evaluate(async (): Promise<WebAppManifest | null> => {
    const href = document.querySelector<HTMLLinkElement>('link[rel="manifest"]')?.href;
    if (!href) return null;
    const response = await fetch(href);
    return response.ok ? ((await response.json()) as WebAppManifest) : null;
  });
}

test("production install metadata uses the Штиль brand and patient tokens", async ({ page }) => {
  await mockAssessmentApi(page.context());
  await page.goto("/patient/");

  await expect(page).toHaveTitle("Штиль");
  const manifest = await readManifest(page);
  expect(manifest).not.toBeNull();
  if (!manifest) throw new Error("The production page did not expose a valid web app manifest");
  expect(manifest).toMatchObject({
    name: "Штиль",
    short_name: "Штиль",
    description: "Отметки о самочувствии и связь с лечащим врачом",
    theme_color: themeColors.patientCool.primary,
    background_color: themeColors.patientCool.canvas,
  });
  await expect(page.locator('meta[name="description"]')).toHaveAttribute(
    "content",
    "Отметки о самочувствии и связь с лечащим врачом",
  );
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute(
    "content",
    themeColors.patientCool.primary,
  );
});

test("production install icons render the supplied patient mark at declared dimensions", async ({
  page,
}) => {
  await mockAssessmentApi(page.context());
  await page.goto("/patient/");
  const manifest = await readManifest(page);
  expect(manifest).not.toBeNull();
  if (!manifest) throw new Error("The production page did not expose a valid web app manifest");

  for (const icon of manifest.icons) {
    const actual = await page.evaluate(
      async ({ src, isMaskable }) => {
        const image = new Image();
        image.src = new URL(src, window.location.href).href;
        await image.decode();
        const canvas = document.createElement("canvas");
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas 2D context is unavailable");
        context.drawImage(image, 0, 0);
        const readPixel = (x: number, y: number) => {
          const pixel = Array.from(context.getImageData(x, y, 1, 1).data).slice(0, 3);
          return `#${pixel.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
        };
        const artworkScale = isMaskable ? 0.8 : 1;
        const artworkOffset = ((1 - artworkScale) * image.naturalWidth) / 2;
        const sampleAtArtworkPoint = (coordinate: number) =>
          Math.floor(artworkOffset + (coordinate / 64) * image.naturalWidth * artworkScale);
        return {
          dimensions: `${image.naturalWidth}x${image.naturalHeight}`,
          mark: readPixel(sampleAtArtworkPoint(32), sampleAtArtworkPoint(26)),
          line: readPixel(sampleAtArtworkPoint(32), sampleAtArtworkPoint(39.5)),
        };
      },
      { src: icon.src, isMaskable: icon.purpose === "maskable" },
    );
    expect(actual.dimensions).toBe(icon.sizes);
    expect(actual.mark).toBe("#f2a57c");
    expect(actual.line).toBe("#1f5f77");

    if (icon.purpose === "maskable") {
      const outsideSafeZone = await page.evaluate(
        async ({ src, backgroundColor }) => {
          const image = new Image();
          image.src = new URL(src, window.location.href).href;
          await image.decode();
          const canvas = document.createElement("canvas");
          canvas.width = image.naturalWidth;
          canvas.height = image.naturalHeight;
          const context = canvas.getContext("2d");
          if (!context) throw new Error("Canvas 2D context is unavailable");
          context.drawImage(image, 0, 0);

          const background = [
            Number.parseInt(backgroundColor.slice(1, 3), 16),
            Number.parseInt(backgroundColor.slice(3, 5), 16),
            Number.parseInt(backgroundColor.slice(5, 7), 16),
          ];
          const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
          const center = canvas.width / 2;
          const safeRadius = canvas.width * 0.4;
          const examples: Array<{ x: number; y: number }> = [];
          let count = 0;

          for (let y = 0; y < canvas.height; y += 1) {
            for (let x = 0; x < canvas.width; x += 1) {
              const offset = (y * canvas.width + x) * 4;
              const isBackground =
                pixels[offset] === background[0] &&
                pixels[offset + 1] === background[1] &&
                pixels[offset + 2] === background[2];
              if (isBackground) continue;

              const dx = x + 0.5 - center;
              const dy = y + 0.5 - center;
              if (dx * dx + dy * dy <= safeRadius * safeRadius) continue;
              count += 1;
              if (examples.length < 5) examples.push({ x, y });
            }
          }

          return { count, examples };
        },
        { src: icon.src, backgroundColor: themeColors.patientCool.canvas },
      );
      expect(
        outsideSafeZone,
        "all foreground pixels must fit inside the 80% maskable circle",
      ).toEqual({
        count: 0,
        examples: [],
      });
    }
  }
  expect(manifest.icons).toContainEqual(
    expect.objectContaining({ sizes: "512x512", purpose: "maskable" }),
  );

  const appleTouchIconSrc = await page.locator('link[rel="apple-touch-icon"]').getAttribute("href");
  if (!appleTouchIconSrc) throw new Error("The production page did not expose an Apple touch icon");
  const appleTouchIconPixels = await page.evaluate(async (src) => {
    const image = new Image();
    image.src = new URL(src, window.location.href).href;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D context is unavailable");
    context.drawImage(image, 0, 0);
    const readPixel = (x: number, y: number) => {
      const pixel = Array.from(context.getImageData(x, y, 1, 1).data).slice(0, 3);
      return `#${pixel.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
    };
    return {
      dimensions: `${image.naturalWidth}x${image.naturalHeight}`,
      mark: readPixel(
        Math.floor(image.naturalWidth / 2),
        Math.floor((26 / 64) * image.naturalHeight),
      ),
      line: readPixel(
        Math.floor(image.naturalWidth / 2),
        Math.floor((39.5 / 64) * image.naturalHeight),
      ),
    };
  }, appleTouchIconSrc);
  expect(appleTouchIconPixels).toEqual({
    dimensions: "180x180",
    mark: "#f2a57c",
    line: "#1f5f77",
  });
});

test("production PWA keeps local Onest font files available without a network", async ({
  page,
  context,
}) => {
  await mockAssessmentApi(page.context());
  const fontRequests: string[] = [];
  page.on("request", (request) => {
    if (/\.woff2(?:[?#]|$)/i.test(request.url())) fontRequests.push(request.url());
  });

  await page.goto("/patient/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller)))
    .toBe(true);

  const loadedOnestFaces = await page.evaluate(async () => {
    const faces = await document.fonts.load('16px "Onest"', "Штиль");
    await document.fonts.ready;
    return faces.map((face) => ({ family: face.family, status: face.status }));
  });
  expect(loadedOnestFaces.length).toBeGreaterThan(0);
  expect(
    loadedOnestFaces.every((face) => face.family === "Onest" && face.status === "loaded"),
  ).toBe(true);
  expect(fontRequests.length).toBeGreaterThan(0);
  expect(fontRequests.every((url) => new URL(url).origin === new URL(page.url()).origin)).toBe(
    true,
  );

  const precachedFonts = await page.evaluate(async () => {
    const cacheNames = await caches.keys();
    const cachedUrls = await Promise.all(
      cacheNames.map(async (name) => {
        const cache = await caches.open(name);
        return (await cache.keys()).map((request) => request.url);
      }),
    );
    return cachedUrls.flat().filter((url) => /\.woff2(?:[?#]|$)/i.test(url));
  });
  expect(precachedFonts.length).toBeGreaterThan(0);

  await context.setOffline(true);
  const fontsAvailableOffline = await page.evaluate(async (urls) => {
    const responses = await Promise.all(
      urls.map(async (url) => {
        try {
          return (await fetch(url)).ok;
        } catch {
          return false;
        }
      }),
    );
    return responses.every(Boolean);
  }, precachedFonts);
  expect(fontsAvailableOffline).toBe(true);
});

test("doctor production page uses the Штиль title and supplied physician favicon", async ({
  page,
}) => {
  await mockAssessmentApi(page.context(), { user: null });
  await page.goto("http://127.0.0.1:5176/doctor/");

  await expect(page).toHaveTitle("Штиль — кабинет врача");
  const favicon = await page.locator('link[rel="icon"]').evaluate((element) => ({
    href: (element as HTMLLinkElement).href,
    type: (element as HTMLLinkElement).type,
  }));
  expect(favicon.type).toBe("image/svg+xml");

  const actual = await page.evaluate(async (src) => {
    const image = new Image();
    image.src = src;
    await image.decode();
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas 2D context is unavailable");
    context.drawImage(image, 0, 0);
    const readPixel = (y: number) => {
      const pixel = Array.from(context.getImageData(32, y, 1, 1).data).slice(0, 3);
      return `#${pixel.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`;
    };
    return {
      dimensions: `${image.naturalWidth}x${image.naturalHeight}`,
      mark: readPixel(26),
      line: readPixel(40),
    };
  }, favicon.href);
  expect(actual.dimensions).toBe("64x64");
  expect(actual.mark).toBe("#1f5f77");
  expect(actual.line).toBe("#151d23");
});
