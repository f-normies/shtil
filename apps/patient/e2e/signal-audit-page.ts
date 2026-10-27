/**
 * Сбор фактов о видимой странице для проверки #36. Функция `collect` выполняется в браузере
 * через `page.evaluate`, поэтому самодостаточна: без импортов и внешних переменных.
 * Цвета нормализуются через холст в `rgba(…)`; коэффициенты контраста считает Node.
 */

export type Cabinet = "doctor" | "patient";

/** Цвет и фон, слоями от элемента к корню: полупрозрачные слои складывает Node. */
export type Sample = {
  kind: "text" | "icon" | "line";
  where: string;
  fg: string;
  bg: string[];
};

export type Finding = { rule: string; where: string; message: string };

export type PageFacts = { samples: Sample[]; findings: Finding[] };

export function collect(cabinet: Cabinet): PageFacts {
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("no canvas");
  const rgba = (css: string): string => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "rgba(0, 0, 0, 0)";
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    return `rgba(${r}, ${g}, ${b}, ${Math.round(((a ?? 0) / 255) * 1000) / 1000})`;
  };
  const token = (name: string) =>
    rgba(getComputedStyle(document.documentElement).getPropertyValue(name).trim());
  // Уровень узнаём и по светлым фонам и рамкам уровня: плашка тоже несёт смысл цвета.
  const REQUIRED = new Set(
    ["--color-required", "--color-required-soft", "--color-required-line"].map(token),
  );
  const ADVISED = new Set(
    ["--color-advised", "--color-advised-soft", "--color-advised-line"].map(token),
  );
  const PRIMARY_TEXT = token("--color-primary-text");
  // Цвета значков `StatusIcon` из SVG-файлов UI-кита (`packages/ui/src/assets/signal-*.svg`).
  // Vite встраивает их в `src` как data:-адрес, поэтому значок узнаём по `data-status`.
  const ICON_FILL: Record<string, string> = {
    required: "rgba(168, 43, 25, 1)",
    advised: "rgba(122, 68, 0, 1)",
    proposed: "rgba(31, 95, 119, 1)",
    confirmed: "rgba(35, 112, 63, 1)",
  };
  const iconStatus = (img: HTMLImageElement): string | null =>
    img.classList.contains("ui-status-icon") ? (img.dataset.status ?? null) : null;
  const iconFill = (img: HTMLImageElement): string | null => {
    const status = iconStatus(img);
    if (!status) return null;
    // Выбранная строка: белый восьмиугольник на primary.
    return img.dataset.accent ? "rgba(255, 255, 255, 1)" : (ICON_FILL[status] ?? null);
  };

  const samples: Sample[] = [];
  const findings: Finding[] = [];

  const visible = (el: Element): boolean => {
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || Number(style.opacity) === 0) return false;
    return el.checkVisibility({ opacityProperty: true, visibilityProperty: true });
  };
  const label = (el: Element): string => {
    const own =
      el.getAttribute("aria-label") ??
      (el instanceof HTMLImageElement ? el.alt : null) ??
      el.textContent ??
      "";
    const text = own.replace(/\s+/g, " ").trim().slice(0, 60);
    return `${el.tagName.toLowerCase()} «${text}»`;
  };
  const backgrounds = (el: Element): string[] => {
    const layers: string[] = [];
    for (let node: Element | null = el; node; node = node.parentElement) {
      const color = rgba(getComputedStyle(node).backgroundColor);
      if (color.endsWith(", 0)")) continue;
      layers.push(color);
      if (color.endsWith(", 1)")) break;
    }
    return layers;
  };
  // Текст только для экранной читалки (`sr-only`): на экране его нет.
  const hiddenText = (n: Node) => n.parentElement?.closest(".sr-only") != null;
  const textOf = (el: Element, keep: (n: Node) => boolean): string => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const parts: string[] = [];
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (keep(n)) parts.push(n.textContent ?? "");
    }
    return parts.join(" ");
  };
  // Подписи для экранной читалки: `aria-label`, `alt` и `sr-only` у элемента и внутри.
  const spoken = (el: Element): string => {
    const nodes = [el, ...el.querySelectorAll("[aria-label], img[alt]")];
    return [
      ...nodes.map(
        (n) =>
          `${n.getAttribute("aria-label") ?? ""} ${n instanceof HTMLImageElement ? n.alt : ""}`,
      ),
      textOf(el, hiddenText),
    ]
      .join(" ")
      .toLowerCase();
  };
  // Всплывающие подсказки `title` у элемента и внутри.
  const titles = (el: Element): string =>
    [el, ...el.querySelectorAll("[title]")]
      .map((n) => n.getAttribute("title") ?? "")
      .join(" ")
      .toLowerCase();
  // Текст на экране: видимые текстовые узлы без `sr-only`.
  const seen = (el: Element): string =>
    textOf(el, (n) => !hiddenText(n) && n.parentElement !== null && visible(n.parentElement))
      .replace(/\s+/g, " ")
      .toLowerCase();
  // Всё, что передаёт смысл словами: текст, подписи и подсказки.
  const words = (el: Element): string => `${seen(el)} ${spoken(el)} ${titles(el)}`;
  // Правило 3: слово видно на экране или есть и в подписи для читалки, и в подсказке.
  // Слово целиком: «необязательно» не засчитывается как «обязательно».
  const has = (text: string, word: string) => new RegExp(`(^|[^а-яё])${word}`).test(text);
  const named = (el: Element, word: string): boolean =>
    has(seen(el), word) || (has(spoken(el), word) && has(titles(el), word));
  // Форма значка: SVG-файл UI-кита или иконка lucide по контуру (lucide 1.x не ставит класс).
  const OCTAGON = "M15.312 2a2";
  const ARROW = "m12 16 4-4-4-4";
  const hasShape = (el: Element, level: "required" | "advised"): boolean => {
    const path = level === "required" ? OCTAGON : ARROW;
    const nodes = [el, ...el.querySelectorAll("img, svg")];
    return nodes.some(
      (n) =>
        (n instanceof HTMLImageElement && iconStatus(n) === level) ||
        (n instanceof SVGSVGElement && n.querySelector(`path[d^="${path}"]`) !== null),
    );
  };
  // Предки элемента до ближайшего смыслового блока включительно: карточка, строка, ссылка,
  // диалог, плашка, график. Подпись уровня у блока относится ко всему его содержимому.
  // `li` не блок: пункты шкалы ответа и строки фактов лежат внутри подписанной карточки.
  const BLOCK = "article, a, tr, [role='option'], [role='dialog'], [role='alert'], [role='img']";
  const upToBlock = (el: Element): Element[] => {
    const chain: Element[] = [];
    for (let n: Element | null = el; n && n !== document.body; n = n.parentElement) {
      chain.push(n);
      if (n.matches(BLOCK)) return chain;
    }
    // Блока нет: ближайшая секция, иначе только сам элемент — не вся страница.
    const end = chain.findIndex((n) => n.matches("section"));
    return chain.slice(0, end >= 0 ? end + 1 : 1);
  };
  const WORD = { required: "обязательно", advised: "желательно" } as const;
  // Свой словарь у проверок по инструкции (кадр «Врач v4 · 10»): «Не пройдена» — крестик и
  // слово красным. Это статус проверки, не уровень сигнала: значок уровня ему не нужен.
  const FAILED = "не пройден";
  const levelOf = (color: string) =>
    REQUIRED.has(color) ? "required" : ADVISED.has(color) ? "advised" : null;

  const all = [...document.body.querySelectorAll("*")].filter(
    (el) => !el.closest("svg") || el instanceof SVGSVGElement,
  );
  const shown = all.filter(visible);

  for (const el of shown) {
    const style = getComputedStyle(el);
    const isSvg = el instanceof SVGSVGElement;
    const isImg = el instanceof HTMLImageElement;
    const ownText = [...el.childNodes].some(
      (n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim() !== "",
    );
    const disabled = el.closest(":disabled, [aria-disabled='true']") !== null;
    const where = label(el);

    // Контраст: текст 4,5:1, значки 3:1.
    if (ownText && !disabled) {
      samples.push({ kind: "text", where, fg: rgba(style.color), bg: backgrounds(el) });
    }
    if (isSvg) {
      samples.push({ kind: "icon", where, fg: rgba(style.color), bg: backgrounds(el) });
    }
    if (isImg) {
      const fill = iconFill(el);
      if (fill) samples.push({ kind: "icon", where, fg: fill, bg: backgrounds(el) });
      // Правило 7: у картинки есть `alt`; пустой — только у украшения рядом со словом.
      if (!el.hasAttribute("alt")) {
        findings.push({ rule: "7", where, message: "у картинки нет атрибута alt" });
      }
    }
    if (isSvg && el.getAttribute("aria-hidden") !== "true" && !el.getAttribute("aria-label")) {
      const named = el.closest("[role='img'][aria-label]");
      if (!named)
        findings.push({ rule: "7", where, message: "у значка SVG нет подписи и aria-hidden" });
    }

    // Правила 1–3: цвет уровня всегда рядом со значком нужной формы и словом.
    const colors = [
      (isSvg || ownText) && rgba(style.color),
      rgba(style.backgroundColor),
      style.borderTopStyle !== "none" && Number.parseFloat(style.borderTopWidth) > 0
        ? rgba(style.borderTopColor)
        : null,
      style.borderLeftStyle !== "none" && Number.parseFloat(style.borderLeftWidth) > 0
        ? rgba(style.borderLeftColor)
        : null,
    ];
    let level: "required" | "advised" | null =
      colors.map((c) => (c ? levelOf(c) : null)).find(Boolean) ?? null;
    if (isImg) {
      const status = iconStatus(el);
      if (status === "required" || status === "advised") level = status;
    }
    if (!level) continue;
    if (cabinet === "patient" && level === "required") {
      findings.push({
        rule: "пациент",
        where,
        message: "красный цвет у пациента вне экрана помощи",
      });
      continue;
    }
    // Ближайший предок с текстом или подписью — группа «значок + слово». Если в ней есть
    // значок, уровень обязан читаться прямо в ней. Цветной текст или линия без значка
    // (порог на шкале ответа) относятся к подписи всего блока.
    const chain = upToBlock(el);
    const group = chain.find((n) => seen(n).trim() !== "" || spoken(n).trim() !== "");
    const iconIn = (n: Element) =>
      n instanceof SVGSVGElement ||
      n instanceof HTMLImageElement ||
      n.querySelector("svg, img") !== null;
    const fits = (n: Element) =>
      (named(n, WORD[level]) && hasShape(n, level)) ||
      (level === "required" && named(n, FAILED) && iconIn(n));
    const ok = group && iconIn(group) ? fits(group) : chain.some(fits);
    if (!ok) {
      findings.push({
        rule: level === "required" ? "1, 3" : "2, 3",
        where,
        message: `цвет «${WORD[level]}» без значка нужной формы и слова рядом`,
      });
    }
  }

  // Правило 4: предложение ждёт решения — пунктир primary-text и «Предложение системы».
  // Рамка предложения: пунктир со всех сторон или пунктирный outline. Пунктир с одной
  // стороны — линия графика или шкалы, не рамка.
  const dashed = (el: Element) => {
    const s = getComputedStyle(el);
    const sides = [
      [s.borderTopStyle, s.borderTopWidth],
      [s.borderRightStyle, s.borderRightWidth],
      [s.borderBottomStyle, s.borderBottomWidth],
      [s.borderLeftStyle, s.borderLeftWidth],
    ];
    const frame = sides.every(
      ([style, width]) => style === "dashed" && Number.parseFloat(width ?? "0") > 0,
    );
    return frame || (s.outlineStyle === "dashed" && Number.parseFloat(s.outlineWidth) > 0);
  };
  const frameColor = (el: Element) => {
    const s = getComputedStyle(el);
    return rgba(s.outlineStyle === "dashed" ? s.outlineColor : s.borderTopColor);
  };
  for (const el of shown) {
    if (!(el instanceof HTMLElement) || !dashed(el)) continue;
    const where = label(el);
    const color = frameColor(el);
    samples.push({ kind: "line", where, fg: color, bg: backgrounds(el.parentElement ?? el) });
    if (color !== PRIMARY_TEXT) {
      findings.push({ rule: "4", where, message: `пунктир цвета ${color}, нужен primary-text` });
    }
    // Подпись — у самой рамки или у блока вокруг: строки внутри подписанного предложения.
    const context = [...upToBlock(el), el.parentElement?.closest("section, article, main")];
    const labelled = (n: Element) =>
      named(n, "предложение системы") || named(n, "предложения системы");
    if (!context.some((n) => n && labelled(n))) {
      findings.push({ rule: "4", where, message: "пунктир без подписи «Предложение системы»" });
    }
  }
  for (const badge of shown.filter((el) => el.matches(".ui-status-badge[data-status]"))) {
    const status = badge.getAttribute("data-status");
    const where = label(badge);
    const text = words(badge);
    const inDashed = (() => {
      for (let n = badge.parentElement; n && n !== document.body; n = n.parentElement) {
        if (dashed(n)) return true;
      }
      return false;
    })();
    if (status === "proposed") {
      if (!text.includes("предложение системы")) {
        findings.push({
          rule: "4",
          where,
          message: "значок предложения без слов «Предложение системы»",
        });
      }
      if (!inDashed)
        findings.push({ rule: "4", where, message: "предложение не обведено пунктиром" });
    }
    if (status === "confirmed") {
      if (!text.includes("подтверждено врачом")) {
        findings.push({ rule: "4", where, message: "значок без слов «Подтверждено врачом»" });
      }
      if (inDashed) findings.push({ rule: "4", where, message: "подтверждённое в пунктире" });
    }
  }
  return { samples, findings };
}
