/**
 * План снимка из перечня страниц (`figma/list-page.js`): какие кадры клонировать, куда
 * класть, какие аннотации к ним относятся.
 */

import { assignAnnotations, type Box, frameDir } from "./layout.ts";
import type { AnnotationText } from "./notes.ts";

export interface ListedNode extends Box {
  readonly type: string;
  readonly children?: readonly ListedNode[];
  readonly caption?: string;
  readonly texts?: readonly AnnotationText[];
}

export interface Listing {
  readonly page: { readonly id: string; readonly name: string };
  readonly nodes: readonly ListedNode[];
}

export type FrameKind = "screen" | "board" | "component";

export interface FrameEntry {
  readonly id: string;
  readonly name: string;
  /** Тип узла Figma: FRAME, COMPONENT, COMPONENT_SET. */
  readonly type?: string;
  readonly page: string;
  readonly kind: FrameKind;
  readonly dir: string;
  readonly width: number;
  readonly height: number;
  readonly caption?: string;
}

export interface PageNotes {
  readonly page: string;
  /** Аннотации по id кадра. */
  readonly byFrame: Record<string, string[]>;
  /** Общие заметки страницы: аннотации без экрана, «Как читать макет», подписи групп. */
  readonly pageLevel: string[];
}

const SCREEN = /^(Врач v4|Пациент) · /;
const ANNOTATION = /^Аннотация/;
const PAGE_NOTE = /^(Как читать|Подпись · )/;
const BOARD_ON_PAGE = /^Дневник · Доска/;
const BOARD_CONTAINERS = new Set(["Врач v4", "Пациент"]);

function entry(page: string, kind: FrameKind, node: ListedNode, parents: string[]): FrameEntry {
  return {
    id: node.id,
    name: node.name,
    type: node.type,
    page,
    kind,
    dir: frameDir({ page, name: node.name, parents }),
    width: node.width,
    height: node.height,
    ...(node.caption ? { caption: node.caption } : {}),
  };
}

/** Разделы досок «Компонентов»; раздел со своими частями раскрывается в части. */
function componentFrames(nodes: readonly ListedNode[]): FrameEntry[] {
  const frames: FrameEntry[] = [];
  for (const node of nodes) {
    if (node.name.startsWith("Знак/")) continue;
    if (node.name === "Компоненты") {
      for (const child of node.children ?? [])
        frames.push(entry("Компоненты", "component", child, ["Компоненты"]));
      continue;
    }
    if (!BOARD_CONTAINERS.has(node.name)) {
      frames.push(entry("Компоненты", "component", node, []));
      continue;
    }
    for (const section of node.children ?? []) {
      if (section.name === "Шапка доски") continue;
      if (!section.children) {
        frames.push(entry("Компоненты", "component", section, [node.name]));
        continue;
      }
      for (const part of section.children) {
        frames.push(entry("Компоненты", "component", part, [node.name, section.name]));
      }
    }
  }
  return frames;
}

/** План снимка: кадры с каталогами и аннотации по кадрам; одинаковый каталог у двух кадров — ошибка. */
export function planSnapshot(listings: readonly Listing[]): {
  frames: FrameEntry[];
  notes: PageNotes[];
} {
  const frames: FrameEntry[] = [];
  const notes: PageNotes[] = [];
  for (const { page, nodes } of listings) {
    if (page.name === "Компоненты") {
      frames.push(...componentFrames(nodes));
      notes.push({ page: page.name, byFrame: {}, pageLevel: [] });
      continue;
    }
    const screens = nodes.filter((node) => SCREEN.test(node.name) && node.type === "FRAME");
    const boards = nodes.filter((node) => BOARD_ON_PAGE.test(node.name));
    frames.push(...screens.map((node) => entry(page.name, "screen", node, [])));
    frames.push(...boards.map((node) => entry(page.name, "board", node, [])));
    const annotations = nodes.filter((node) => ANNOTATION.test(node.name));
    const { page: pageLevel = [], ...byFrame } = assignAnnotations(
      page.name,
      [...screens, ...boards],
      annotations,
    );
    const extra = nodes
      .filter((node) => PAGE_NOTE.test(node.name) && node.type === "FRAME")
      .map((node) => node.id);
    notes.push({ page: page.name, byFrame, pageLevel: [...pageLevel, ...extra] });
  }
  const owners = new Map<string, string>();
  for (const frame of frames) {
    const owner = owners.get(frame.dir);
    if (owner) throw new Error(`Каталог ${frame.dir} достался двум кадрам: ${owner} и ${frame.id}`);
    owners.set(frame.dir, frame.id);
  }
  return { frames, notes };
}
