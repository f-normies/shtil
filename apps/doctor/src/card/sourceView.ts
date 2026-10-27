/**
 * Блок ссылки в инспекторе источника (спека #106, раздел 5): сведения о документе,
 * данные ГРЛС у инструкции, фрагменты раздела с пометками версии и выделенной цитатой.
 * Чистые функции, без React.
 */
import type { Schemas } from "@compass/api-client";
import { type QuoteParts, splitQuote } from "./guidance";
import type { SourceRef } from "./sources";

type SourceView = Schemas["SourceView"];
type Registry = NonNullable<SourceView["registry"]>;
type Section = SourceView["sections"][number];

export type Field = { label: string; value: string };

/**
 * Фрагмент текста. `heading` — путь заголовков, если он сменился после прошлого фрагмента;
 * `notes` — пометки версии, если они сменились. `parts` — текст с выделенной цитатой.
 */
export type FragmentRow = {
  id: string;
  heading: string | null;
  notes: string[];
  text: string;
  parts: QuoteParts | null;
};

export type SourceBlock = {
  title: string;
  fields: Field[];
  supplementary: boolean;
  link: { label: string; url: string };
  found: boolean;
  fragments: FragmentRow[];
};

/** `missing` — 404: источника нет в манифесте или фрагмента нет, повтор не поможет. */
export type BlockState = "loading" | "error" | "missing" | "not-found" | "found";

const REGISTRY_STATUS: Record<Exclude<Registry["status"], "unknown">, string> = {
  active: "действует",
  inactive: "не действует",
  suspended: "приостановлено",
};

/** Состояние блока по запросу. Повтор после ошибки показывает загрузку. */
export function blockState(query: {
  isError: boolean;
  isFetching: boolean;
  data: SourceView | undefined;
  error: unknown;
}): BlockState {
  if (query.data) {
    return query.data.found ? "found" : "not-found";
  }
  if (!query.isError || query.isFetching) {
    return "loading";
  }
  return statusOf(query.error) === 404 ? "missing" : "error";
}

/** Что показать о ссылке: документ, раздел, версия, издатель, ГРЛС и текст. */
export function sourceBlock(view: SourceView, ref: SourceRef): SourceBlock {
  const { source, registry } = view;
  const section = view.locator ?? ref.locator ?? sectionsText(view.sections);
  const fields: Field[] = [
    { label: "Раздел", value: section },
    { label: "Версия", value: versionText(source.version, source.date) },
    { label: "Издатель", value: source.publisher },
    { label: "Номер РУ", value: registry ? registryText(registry) : "" },
  ].filter((f) => f.value !== "");
  return {
    title: documentTitle(source),
    fields,
    supplementary: source.supplementary,
    link: {
      label: source.kind === "drug_label" && registry ? "Открыть в реестре" : "Открыть документ",
      url: source.url,
    },
    found: view.found,
    fragments: fragmentRows(view, ref.quote),
  };
}

/** Название КР в манифесте — только название болезни: вид документа дописывается. */
export function documentTitle(source: { kind: string; title: string }): string {
  return source.kind === "clinical_guideline"
    ? `Клинические рекомендации «${source.title}»`
    : source.title;
}

/** «0010 от 14.10.2026». */
export function versionText(version: string, date: string): string {
  return `${version} от ${dayText(date)}`;
}

/** «ЛП-№(002445)-(РГ-RU), действует по ГРЛС на 16.10.2026». */
export function registryText(registry: Registry): string {
  const day = dayText(registry.snapshot_date);
  if (registry.status === "unknown") {
    return `${registry.reg_number}, статус в ГРЛС на ${day} не указан`;
  }
  return `${registry.reg_number}, ${REGISTRY_STATUS[registry.status]} по ГРЛС на ${day}`;
}

function fragmentRows(view: SourceView, quote: string | undefined): FragmentRow[] {
  let heading: string | null = null;
  let notes = "";
  return view.sections
    .flatMap((s) => s.fragments)
    .map((f) => {
      const path = f.heading_path || f.section_title;
      const own = [
        f.document.status === "superseded" ? "Прежняя версия документа" : "",
        f.document.version !== view.source.version ? `Текст — версия ${f.document.version}` : "",
      ].filter((n) => n !== "");
      const row: FragmentRow = {
        id: f.id,
        heading: path !== heading ? path : null,
        notes: own.join("|") !== notes ? own : [],
        text: f.text,
        parts: quote ? splitQuote(f.text, quote) : null,
      };
      heading = path;
      notes = own.join("|");
      return row;
    });
}

/** Код ответа из тела ошибки RFC 9457: клиент не отдаёт заголовки ошибки. */
function statusOf(error: unknown): unknown {
  return typeof error === "object" && error !== null && "status" in error ? error.status : null;
}

/** Разделы фрагмента по id: номер и заголовок. */
function sectionsText(sections: readonly Section[]): string {
  return sections.map((s) => (s.number ? `${s.number} ${s.title}`.trim() : s.title)).join("; ");
}

/** `YYYY-MM-DD` → `ДД.ММ.ГГГГ` без часового пояса: это календарная дата. */
function dayText(iso: string): string {
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}
