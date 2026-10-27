import { Alert, BrandMark, Button } from "@compass/ui";
import type { ReactNode } from "react";

/**
 * Запасные экраны корневого маршрута вместо английских заглушек TanStack Router.
 * Ссылки — обычные `<a>`: экраны показываются и когда маршрутизатор не поднялся.
 */
function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-4 px-6 py-8">
      <BrandMark persona="doctor" decorative />
      <h1 className="text-2xl font-semibold text-ink">{title}</h1>
      {children}
    </main>
  );
}

/** Маршрут не загрузился: нет связи или сервер ответил ошибкой. Данные кэша не трогаем. */
export function LoadFailed() {
  return (
    <Shell title="Кабинет врача">
      <Alert tone="error">Не удалось загрузить кабинет. Проверьте связь и обновите страницу.</Alert>
      <Button className="self-start" onClick={() => window.location.reload()}>
        Обновить
      </Button>
    </Shell>
  );
}

/** Адреса нет: ведём к пациентам, без сессии оттуда откроется вход. */
export function NotFound() {
  return (
    <Shell title="Такой страницы нет">
      <p className="text-[15px] text-ink-muted">
        Проверьте адрес или вернитесь к списку пациентов.
      </p>
      <a href="/doctor/" className="ui-button self-start" data-variant="primary">
        К пациентам
      </a>
    </Shell>
  );
}
