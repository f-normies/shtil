/** Разделы карточки пациента в тулбаре (спека #13, раздел 10.9). */

export const SECTIONS = [
  { value: "/patients/$patientId", label: "Обзор" },
  { value: "/patients/$patientId/scales", label: "Шкалы" },
  { value: "/patients/$patientId/therapy", label: "Терапия" },
  { value: "/patients/$patientId/diary", label: "Дневник" },
  { value: "/patients/$patientId/adherence", label: "Приём" },
  { value: "/patients/$patientId/proposals", label: "Предложения" },
] as const;

export type SectionPath = (typeof SECTIONS)[number]["value"];
/** У каждого раздела есть своя страница. */
export type SectionRoute = SectionPath;

const OVERVIEW = SECTIONS[0].value;

/** Выбранный раздел по открытой странице. Страницы вне разделов (истории болезни) — «Обзор». */
export function sectionOf(routeId: string | undefined): SectionPath {
  const path = routeId?.replace(/^\/_doctor/, "").replace(/\/$/, "") ?? "";
  return SECTIONS.find((s) => s.value !== OVERVIEW && path.startsWith(s.value))?.value ?? OVERVIEW;
}

/** Разделы по доступу: заместителю по сигналу — только «Обзор» и «Шкалы» (спека #56). */
export function sectionsFor(access: "lead" | "deputy"): readonly (typeof SECTIONS)[number][] {
  return access === "deputy" ? SECTIONS.slice(0, 2) : SECTIONS;
}
