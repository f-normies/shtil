import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import { test } from "node:test";

const cssPath = new URL("../src/typography.css", import.meta.url);
const stylesCss = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const packagePath = new URL("../package.json", import.meta.url);
const sourcePath = new URL("../../../docs/design/figma/tokens/variables.json", import.meta.url);

const css = [
  stylesCss,
  await readFile(new URL("../src/tokens.css", import.meta.url), "utf8"),
  await readFile(cssPath, "utf8"),
].join("\n");
const cssWithoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
const uiPackage = JSON.parse(await readFile(packagePath, "utf8"));
const collections = JSON.parse(await readFile(sourcePath, "utf8"));

function rulesFor(selector) {
  return [...cssWithoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((match) => match[1].trim() === selector)
    .map((match) =>
      Object.fromEntries(
        match[2]
          .split(";")
          .map((declaration) => declaration.trim())
          .filter(Boolean)
          .map((declaration) => {
            const separator = declaration.indexOf(":");
            return [
              declaration.slice(0, separator).trim(),
              declaration.slice(separator + 1).trim(),
            ];
          }),
      ),
    );
}

function declarationsFor(selector) {
  return Object.assign({}, ...rulesFor(selector));
}

function collection(name) {
  const result = collections.find((item) => item.name === name);
  assert.ok(result, `Figma collection ${name} exists`);
  return result;
}

function cssTokenName(name, prefix) {
  const token = name.replaceAll("/", "-");
  return prefix ? `--${prefix}-${token}` : `--${token}`;
}

function paletteValues() {
  return Object.fromEntries(
    collection("Палитра").variables.map((variable) => [variable.name, variable.values[0]]),
  );
}

function resolveColor(value, palette) {
  return typeof value === "string" ? value : palette[value.alias.replace("Палитра/", "")];
}

function relativeLuminance(hex) {
  const channels = hex
    .match(/[\da-f]{2}/gi)
    .slice(0, 3)
    .map((channel) => parseInt(channel, 16) / 255);
  const linear = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
}

test("package exports self-contained theme tokens and all app palettes", async () => {
  assert.equal(uiPackage.exports["./tokens"], "./src/tokens.ts");
  assert.equal(uiPackage.exports["./assets/*"], "./src/assets/*");
  const { themeColors, paletteColors, spacingTokens, radiusTokens, moodColors } = await import(
    "@compass/ui/tokens"
  );
  const palette = paletteValues();
  const sourceColors = collection("Цвет");
  const modes = ["doctor", "patientCool", "patientWarm"];

  assert.deepEqual(Object.keys(themeColors), modes);
  for (const [modeIndex, mode] of modes.entries()) {
    const expected = Object.fromEntries(
      sourceColors.variables.map((variable) => [
        variable.name
          .slice("color/".length)
          .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()),
        resolveColor(variable.values[modeIndex], palette),
      ]),
    );
    assert.deepEqual(themeColors[mode], expected, `${mode} values match Figma`);
    assert.match(themeColors[mode].canvas, /^#[\da-f]{6}$/i);
    assert.match(themeColors[mode].primary, /^#[\da-f]{6}$/i);
  }

  const expectedPalette = {};
  for (const variable of collection("Палитра").variables) {
    const [family, shade] = variable.name.split("/");
    expectedPalette[family] ??= {};
    expectedPalette[family][shade] = variable.values[0];
  }
  assert.deepEqual(paletteColors, expectedPalette);
  assert.deepEqual(
    spacingTokens,
    Object.fromEntries(
      collection("Отступы").variables.map((variable) => [
        variable.name.replace("/", ""),
        variable.values[0],
      ]),
    ),
  );
  assert.deepEqual(
    radiusTokens,
    Object.fromEntries(
      ["doctor", "patient"].map((mode, modeIndex) => [
        mode,
        Object.fromEntries(
          collection("Форма").variables.map((variable) => [
            variable.name.slice("radius/".length),
            variable.values[modeIndex],
          ]),
        ),
      ]),
    ),
  );
  assert.deepEqual(
    moodColors,
    Object.fromEntries(
      ["warmGood", "greenGood", "calm"].map((mode, modeIndex) => [
        mode,
        collection("Настроение").variables.map((variable) =>
          resolveColor(variable.values[modeIndex], palette),
        ),
      ]),
    ),
  );
  const packageSource = await readFile(new URL("../src/tokens.ts", import.meta.url), "utf8");
  assert.doesNotMatch(packageSource, /docs\/design\/figma/);
});

test("CSS preserves every palette value and spacing expression from Figma", () => {
  const root = declarationsFor(":root");
  const theme = declarationsFor("@theme");
  const palette = collection("Палитра");
  const spacing = collection("Отступы");

  for (const variable of palette.variables) {
    assert.equal(root[cssTokenName(variable.name, "palette")], variable.values[0], variable.name);
  }
  assert.equal(theme["--spacing"], "4px");
  for (const variable of spacing.variables) {
    const multiplier = Number(variable.name.slice("space/".length));
    assert.equal(variable.values[0], multiplier * 4, `${variable.name} is a 4 px step`);
    assert.equal(
      root[cssTokenName(variable.name, "")],
      `calc(var(--spacing) * ${multiplier})`,
      variable.name,
    );
  }
});

test("CSS keeps all semantic aliases and allows explicit nested doctor mode", () => {
  const palette = paletteValues();
  const colors = collection("Цвет");
  const modeSelectors = [
    "@theme",
    '[data-tsvet="patsient-prokhladnaya"]',
    '[data-tsvet="patsient-teplaya"]',
  ];
  const doctorDeclarations = declarationsFor('[data-tsvet="vrach"]');

  for (const variable of colors.variables) {
    const cssName = cssTokenName(variable.name, "");
    for (const [modeIndex, selector] of modeSelectors.entries()) {
      const expected =
        typeof variable.values[modeIndex] === "string"
          ? variable.values[modeIndex]
          : `var(--palette-${variable.values[modeIndex].alias.replace("Палитра/", "").replaceAll("/", "-")})`;
      assert.equal(declarationsFor(selector)[cssName], expected, `${selector} ${variable.name}`);
    }
    const doctorValue =
      typeof variable.values[0] === "string"
        ? variable.values[0]
        : `var(--palette-${variable.values[0].alias.replace("Палитра/", "").replaceAll("/", "-")})`;
    assert.equal(doctorDeclarations[cssName], doctorValue, `nested doctor reset ${variable.name}`);
    assert.match(resolveColor(variable.values[0], palette), /^#[\da-f]{6}([\da-f]{2})?$/i);
  }
});

test("CSS preserves radius modes and all three mood sets", () => {
  const theme = declarationsFor("@theme");
  const patientShape = declarationsFor('[data-forma="patsient"]');
  const doctorShape = declarationsFor('[data-forma="vrach"]');
  const defaultMood = declarationsFor(":root");
  const warmMood = declarationsFor('[data-nastroenie="teplyy-khorosho"]');
  const mood = collection("Настроение");
  const palette = paletteValues();
  const selectors = [
    ":root",
    '[data-nastroenie="zelenyy-khorosho"]',
    '[data-nastroenie="spokoynyy"]',
  ];

  for (const variable of collection("Форма").variables) {
    const property = cssTokenName(variable.name, "");
    assert.equal(theme[property], `${variable.values[0]}px`, `doctor ${variable.name}`);
    assert.equal(patientShape[property], `${variable.values[1]}px`, `patient ${variable.name}`);
    assert.equal(
      doctorShape[property],
      `${variable.values[0]}px`,
      `nested doctor ${variable.name}`,
    );
  }

  for (const variable of mood.variables) {
    const property = cssTokenName(variable.name, "");
    for (const [modeIndex, selector] of selectors.entries()) {
      const value = variable.values[modeIndex];
      const expected = `var(--palette-${value.alias.replace("Палитра/", "").replaceAll("/", "-")})`;
      const actual =
        selector === ":root" ? defaultMood[property] : declarationsFor(selector)[property];
      assert.equal(actual, expected, `${selector} ${variable.name}`);
      assert.ok(palette[value.alias.replace("Палитра/", "")], `mood alias ${value.alias} resolves`);
    }
    const warmValue = variable.values[0];
    assert.equal(
      warmMood[property],
      `var(--palette-${warmValue.alias.replace("Палитра/", "").replaceAll("/", "-")})`,
      `explicit warm mood reset ${variable.name}`,
    );
  }
});

test("used text, control, and signal color pairs meet contrast requirements", async () => {
  assert.equal(uiPackage.exports["./tokens"], "./src/tokens.ts");
  const { themeColors } = await import("@compass/ui/tokens");
  for (const [mode, colors] of Object.entries(themeColors)) {
    for (const foreground of ["ink", "inkMuted", "inkSubtle", "primaryText"]) {
      for (const background of ["canvas", "surface", "surfaceSunken", "primarySoft"]) {
        assert.ok(
          contrastRatio(colors[foreground], colors[background]) >= 4.5,
          `${mode} ${foreground}/${background}`,
        );
      }
    }
    assert.ok(contrastRatio(colors.primaryInk, colors.primary) >= 4.5, `${mode} primary button`);
    for (const [foreground, background] of [
      ["required", "requiredSoft"],
      ["advised", "advisedSoft"],
      ["success", "successSoft"],
      ["danger", "dangerSoft"],
    ]) {
      assert.ok(
        contrastRatio(colors[foreground], colors[background]) >= 4.5,
        `${mode} ${foreground}/${background}`,
      );
    }
    assert.ok(
      contrastRatio(colors.lineStrong, colors.surface) >= 3,
      `${mode} significant control line`,
    );
    assert.ok(contrastRatio(colors.focus, colors.surface) >= 3, `${mode} focus indicator`);
  }
});

test("local Onest files and reusable doctor and patient typography are complete", async () => {
  assert.match(stylesCss, /@import "\.\/tokens\.css";/);
  assert.match(stylesCss, /@import "\.\/typography\.css";/);
  const fontFaces = rulesFor("@font-face");
  assert.equal(fontFaces.length, 7);
  for (const face of fontFaces) {
    assert.equal(face["font-family"], '"Onest"');
    assert.equal(face["font-style"], "normal");
    assert.equal(face["font-display"], "swap");
    assert.equal(face["font-weight"], "100 900");
    assert.ok(face["unicode-range"], "font subset has an explicit unicode range");
    const source = face.src.match(/url\(["']?([^"')]+)["']?\)/)?.[1];
    assert.ok(source?.startsWith("./assets/fonts/"), "font loads from package assets");
    assert.ok((await stat(new URL(source, cssPath))).size > 0, `${source} exists`);
  }
  assert.equal(
    (await readFile(new URL("../src/assets/fonts/LICENSE", import.meta.url), "utf8")).includes(
      "SIL OPEN FONT LICENSE Version 1.1",
    ),
    true,
  );
  assert.doesNotMatch(css, /https?:\/\//i);

  const typography = {
    ".type-doctor-title": ["20px", "26px", "600", "0px"],
    ".type-doctor-callout": ["14px", "20px", "400", "0px"],
    ".type-doctor-callout-strong": ["14px", "20px", "600", "0px"],
    ".type-doctor-heading-large": ["26px", "32px", "600", "-0.5px"],
    ".type-doctor-subtitle": ["13px", "18px", "400", "0px"],
    ".type-doctor-group-label": ["12px", "16px", "600", "0.4px"],
    ".type-doctor-label-lowercase": ["12px", "16px", "600", "0px"],
    ".type-doctor-accent": ["15px", "20px", "600", "0px"],
    ".type-doctor-caption": ["12px", "16px", "400", "0px"],
    ".type-doctor-body": ["15px", "22px", "400", "0px"],
    ".type-doctor-number-large": ["40px", "44px", "600", "-1px"],
    ".type-doctor-number": ["30px", "34px", "600", "-0.6px"],
    ".type-patient-question": ["24px", "30px", "600", "-0.4px"],
    ".type-patient-title": ["22px", "28px", "600", "-0.2px"],
    ".type-patient-button": ["17px", "22px", "600", "0px"],
    ".type-patient-caption": ["13px", "18px", "500", "0.1px"],
    ".type-patient-greeting": ["30px", "36px", "600", "-0.7px"],
    ".type-patient-body-small": ["15px", "20px", "400", "0px"],
    ".type-patient-body": ["17px", "24px", "400", "0px"],
  };
  for (const [selector, [fontSize, lineHeight, fontWeight, letterSpacing]] of Object.entries(
    typography,
  )) {
    const declarations = declarationsFor(selector);
    assert.equal(declarations["font-size"], fontSize, `${selector} font-size`);
    assert.equal(declarations["line-height"], lineHeight, `${selector} line-height`);
    assert.equal(declarations["font-weight"], fontWeight, `${selector} font-weight`);
    assert.equal(declarations["letter-spacing"], letterSpacing, `${selector} letter-spacing`);
  }
});

test("package SVG assets are byte-identical to the Figma snapshot", async () => {
  assert.equal(uiPackage.exports["./assets/*"], "./src/assets/*");
  const sourceDirectory = new URL("../../../docs/design/figma/icons/", import.meta.url);
  const packageDirectory = new URL("../src/assets/", import.meta.url);
  const svgFiles = (await readdir(sourceDirectory)).filter((file) => file.endsWith(".svg")).sort();
  assert.ok(svgFiles.includes("znak-vrach.svg"));
  assert.ok(svgFiles.includes("znak-patsient.svg"));
  assert.ok(svgFiles.includes("signal-obyazatelno.svg"));
  assert.ok(svgFiles.includes("signal-zhelatelno.svg"));
  assert.ok(svgFiles.includes("ikonka-glaz.svg"));
  assert.ok(svgFiles.includes("ikonka-glaz-zakryt.svg"));
  for (const file of svgFiles) {
    assert.deepEqual(
      await readFile(new URL(file, packageDirectory)),
      await readFile(new URL(file, sourceDirectory)),
      file,
    );
  }
});
