import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { themeColors } from "@compass/ui/tokens";
import { chromium } from "@playwright/test";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = resolve(appRoot, "../..");
const publicDirectory = resolve(appRoot, "public");
const sourcePath = resolve(repositoryRoot, "docs/design/figma/icons/znak-patsient.svg");
const outputIcons = [
  { name: "apple-touch-icon.png", size: 180, artworkScale: 1 },
  { name: "icon-192.png", size: 192, artworkScale: 1 },
  { name: "icon-512.png", size: 512, artworkScale: 1 },
  { name: "icon-maskable-512.png", size: 512, artworkScale: 0.8 },
];
const dataUrlPrefix = "data:image/png;base64,";

await mkdir(publicDirectory, { recursive: true });
const sourceSvg = await readFile(sourcePath, "utf8");
const sourceUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sourceSvg)}`;
const browser = await chromium.launch({ headless: true });

try {
  const page = await browser.newPage();
  for (const icon of outputIcons) {
    const pngDataUrl = await page.evaluate(
      async ({ svgUrl, size, background, artworkScale }) => {
        const image = new Image();
        image.src = svgUrl;
        await image.decode();

        const canvas = document.createElement("canvas");
        canvas.width = size;
        canvas.height = size;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas 2D context is unavailable");
        context.fillStyle = background;
        context.fillRect(0, 0, size, size);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        const artworkSize = size * artworkScale;
        const artworkOffset = (size - artworkSize) / 2;
        context.drawImage(image, artworkOffset, artworkOffset, artworkSize, artworkSize);
        return canvas.toDataURL("image/png");
      },
      {
        svgUrl: sourceUrl,
        size: icon.size,
        background: themeColors.patientCool.canvas,
        artworkScale: icon.artworkScale,
      },
    );
    await writeFile(
      resolve(publicDirectory, icon.name),
      Buffer.from(pngDataUrl.slice(dataUrlPrefix.length), "base64"),
    );
  }
} finally {
  await browser.close();
}
