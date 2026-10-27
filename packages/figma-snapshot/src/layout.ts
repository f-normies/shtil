/**
 * Раскладка снимка: каталог каждого кадра и привязка аннотаций к экранам.
 * Пути латиницей: кириллица в именах файлов на macOS ломается из-за NFD-нормализации.
 */

const TRANSLIT: Record<string, string> = {
  а: "a",
  б: "b",
  в: "v",
  г: "g",
  д: "d",
  е: "e",
  ё: "e",
  ж: "zh",
  з: "z",
  и: "i",
  й: "y",
  к: "k",
  л: "l",
  м: "m",
  н: "n",
  о: "o",
  п: "p",
  р: "r",
  с: "s",
  т: "t",
  у: "u",
  ф: "f",
  х: "kh",
  ц: "ts",
  ч: "ch",
  ш: "sh",
  щ: "shch",
  ъ: "",
  ы: "y",
  ь: "",
  э: "e",
  ю: "yu",
  я: "ya",
};
const MAX_SLUG = 48;
const PAGE_DIRS: Record<string, string> = {
  Врач: "doctor",
  Пациент: "patient",
  Компоненты: "components",
};
const BOARD_PREFIXES: Record<string, string> = {
  "Врач v4": "doctor",
  Пациент: "patient",
  Компоненты: "base",
};
const SCREEN_PREFIX = /^(Врач v4|Пациент) · /;
const SCREEN_CODE = /· (\d+[а-я]?) /;

export interface FramePlace {
  readonly page: string;
  readonly name: string;
  /** Имена досок и разделов над кадром на странице «Компоненты», сверху вниз. */
  readonly parents: readonly string[];
}

export interface Box {
  readonly id: string;
  readonly name: string;
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

/** Латинский slug не длиннее 48 знаков, обрезанный по границе слова. */
export function slugify(text: string): string {
  const latin = [...text.toLowerCase()].map((char) => TRANSLIT[char] ?? char).join("");
  const slug = latin.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  if (slug.length <= MAX_SLUG) return slug;
  const cut = slug.slice(0, MAX_SLUG + 1);
  return cut.slice(0, cut.lastIndexOf("-"));
}

const withoutSection = (name: string) => name.replace(/^Раздел · /, "");

/** Каталог кадра относительно `docs/design/figma/`. */
export function frameDir({ page, name, parents }: FramePlace): string {
  const pageDir = PAGE_DIRS[page] ?? slugify(page);
  if (SCREEN_PREFIX.test(name)) return `${pageDir}/${slugify(name.replace(SCREEN_PREFIX, ""))}`;
  if (page !== "Компоненты") return `${pageDir}/${slugify(`board ${name}`)}`;
  const [board, ...sections] = parents;
  const prefix = board ? [BOARD_PREFIXES[board] ?? slugify(board)] : [];
  return `${pageDir}/${slugify([...prefix, ...sections.map(withoutSection), withoutSection(name)].join(" "))}`;
}

function distance(a: Box, b: Box): number {
  const dx = Math.max(0, a.x - (b.x + b.width), b.x - (a.x + a.width));
  const dy = Math.max(0, a.y - (b.y + b.height), b.y - (a.y + a.height));
  return Math.hypot(dx, dy);
}

/**
 * Аннотации по экранам: ключ — id экрана или `page` для общих заметок страницы.
 * На странице врача аннотация называется по экрану («Аннотация · Врач v4 · 02 …»), без
 * кода экрана она относится к странице. На странице пациента аннотации стоят у своих
 * экранов без кода, поэтому берём ближайший экран.
 */
export function assignAnnotations(
  page: string,
  screens: readonly Box[],
  annotations: readonly Box[],
): Record<string, string[]> {
  const assigned: Record<string, string[]> = {};
  const add = (key: string, id: string) => {
    assigned[key] = [...(assigned[key] ?? []), id];
  };
  for (const annotation of annotations) {
    const code = annotation.name.match(SCREEN_CODE)?.[1];
    const byCode = code && screens.find((screen) => screen.name.match(SCREEN_CODE)?.[1] === code);
    if (byCode) {
      add(byCode.id, annotation.id);
      continue;
    }
    if (page === "Врач" || screens.length === 0) {
      add("page", annotation.id);
      continue;
    }
    const nearest = [...screens].sort(
      (a, b) => distance(annotation, a) - distance(annotation, b),
    )[0];
    if (nearest) add(nearest.id, annotation.id);
  }
  return assigned;
}
