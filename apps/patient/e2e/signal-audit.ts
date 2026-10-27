import { expect, type Page, type TestInfo } from "@playwright/test";
import { contrastRatio, flatten } from "./contrast";
import { type Cabinet, collect, type Finding } from "./signal-audit-page";

/**
 * Проверка #36 на открытом экране: правила 1–7 спеки
 * `docs/superpowers/specs/2026-10-24-36-signal-visibility-design.md`.
 * С `EVIDENCE=1` снимок экрана ложится в `docs/evidence/36/<имя>.png`.
 */

// Типов Node в e2e нет: из окружения нужен только флаг снимков.
declare const process: { env: Record<string, string | undefined> };

const MINIMUM = { text: 4.5, icon: 3, line: 3 } as const;

export type Measure = {
  kind: keyof typeof MINIMUM;
  fg: string;
  bg: string;
  ratio: number;
  minimum: number;
  where: string;
};

const hex = (c: readonly number[]) =>
  `#${c
    .slice(0, 3)
    .map((v) => Math.round(v).toString(16).padStart(2, "0"))
    .join("")}`;

/** Проверить экран, приложить замеры к отчёту и упасть со списком всех расхождений. */
export async function auditScreen(
  page: Page,
  testInfo: TestInfo,
  { name, cabinet }: { name: string; cabinet: Cabinet },
): Promise<Measure[]> {
  // Шрифт и картинки загружены, анимации закончены: цвета и снимок устойчивы.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForLoadState("networkidle");
  const facts = await page.evaluate(collect, cabinet);
  const findings: Finding[] = [...facts.findings];
  const unique = new Map<string, Measure>();
  for (const s of facts.samples) {
    const bg = flatten(s.bg);
    const ratio = Math.round(contrastRatio(s.fg, bg) * 100) / 100;
    const key = `${s.kind} ${s.fg} ${hex(bg)}`;
    const measure: Measure = {
      kind: s.kind,
      fg: s.fg,
      bg: hex(bg),
      ratio,
      minimum: MINIMUM[s.kind],
      where: s.where,
    };
    if (!unique.has(key)) unique.set(key, measure);
    if (ratio < MINIMUM[s.kind]) {
      findings.push({
        rule: "6",
        where: s.where,
        message: `контраст ${ratio}:1 (${s.fg} на ${hex(bg)}), нужно ${MINIMUM[s.kind]}:1`,
      });
    }
  }
  const measures = [...unique.values()].sort((a, b) => a.ratio - b.ratio);
  await testInfo.attach(`${name}-contrast.json`, {
    body: JSON.stringify(measures, null, 2),
    contentType: "application/json",
  });
  if (process.env.EVIDENCE === "1") {
    // `rootDir` — каталог e2e; Playwright сам создаёт недостающие каталоги снимка.
    const evidence = `${testInfo.config.rootDir}/../../../docs/evidence/36/${name}.png`;
    await page.screenshot({ path: evidence });
  }
  const seen = new Set<string>();
  const report = findings
    .map((f) => `[${f.rule}] ${f.where}: ${f.message}`)
    .filter((line) => !seen.has(line) && seen.add(line));
  expect(report, `${name}: расхождения с правилами #36`).toEqual([]);
  return measures;
}
