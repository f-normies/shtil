import { createContext, type ReactNode, useContext } from "react";

/**
 * Что показать в инспекторе: заголовок панели и содержимое. `id` — то же содержимое
 * (вопрос к базе знаний): повторное открытие не пересоздаёт его, а переводит фокус.
 */
export type InspectorContent = {
  title: string;
  body: ReactNode;
  id?: string;
  /** Keep content open while the active route remains inside this scope. */
  withinScope?: string;
};

/**
 * Оболочка инспектора справа (спека #106, раздел 3.8). Экран открывает в ней содержимое:
 * источник (#106), позже чат (#33). `trigger` — кнопка, на которую вернётся фокус.
 */
export type InspectorApi = {
  open: (content: InspectorContent, trigger: HTMLElement | null) => void;
  close: () => void;
};

export const InspectorContext = createContext<InspectorApi | null>(null);

export function useInspector(): InspectorApi {
  const api = useContext(InspectorContext);
  if (!api) {
    throw new Error("useInspector работает только внутри <Inspector>");
  }
  return api;
}
