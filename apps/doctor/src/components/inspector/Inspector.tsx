import { Button } from "@compass/ui";
import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { type InspectorApi, type InspectorContent, InspectorContext } from "./context";
import {
  closesOnEscape,
  closesOnScopeChange,
  initialFocus,
  insideModal,
  reopens,
  returnFocus,
} from "./focus";

type Shown = InspectorContent & {
  trigger: HTMLElement | null;
  /** Номер открытия: смена содержимого проявляется заново. */
  version: number;
  /** Повторное открытие того же содержимого: только фокус, без пересоздания. */
  focusRequest: number;
  /** Кнопка была в модальном листе: панель — модальное окно поверх него. */
  modal: boolean;
  scope: string;
};

const MOTION = "motion-safe:animate-inspector-in motion-reduce:animate-fade-in";

/**
 * Окно врача с инспектором справа (спека #106, раздел 3.8; компонент «Врач v4/Инспектор»):
 * панель 320 px на всю высоту, содержимое сдвигается и не перекрывается. Esc и «Закрыть»
 * закрывают панель, фокус возвращается на кнопку, которая её открыла. `scope` — адрес
 * страницы: на другой странице прежнее основание не к месту, панель закрывается.
 */
export function Inspector({ scope, children }: { scope: string; children: ReactNode }) {
  const [shown, setShown] = useState<Shown | null>(null);
  if (shown && shown.scope !== scope) {
    if (closesOnScopeChange(shown, scope)) {
      // Состояние по прошлой странице: React перерисует сразу, без эффекта.
      setShown(null);
    } else {
      // Чат остаётся открытым в пределах карточки; базой дальнейшей навигации становится
      // уже выбранный раздел, чтобы повторное открытие не пересоздало содержимое.
      setShown((current) => (current ? { ...current, scope } : null));
    }
  }
  const trigger = shown?.trigger ?? null;
  const close = useCallback(() => {
    setShown(null);
    returnFocus(trigger);
  }, [trigger]);
  const open = useCallback(
    (content: InspectorContent, from: HTMLElement | null) =>
      setShown((prev) =>
        prev && reopens(prev, content, scope)
          ? { ...prev, trigger: from, focusRequest: prev.focusRequest + 1 }
          : {
              ...content,
              trigger: from,
              version: (prev?.version ?? 0) + 1,
              focusRequest: 0,
              modal: insideModal(from),
              scope,
            },
      ),
    [scope],
  );
  const api = useMemo<InspectorApi>(() => ({ open, close }), [open, close]);
  const inline = shown !== null && !shown.modal;

  useEffect(() => {
    if (!inline) {
      return;
    }
    // На window: поповер и лист успевают забрать Esc раньше (`preventDefault`).
    const onKeyDown = (event: KeyboardEvent) => {
      if (closesOnEscape(event, document.querySelector("dialog:modal") !== null)) {
        close();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [inline, close]);

  return (
    <InspectorContext.Provider value={api}>
      {/* clip: панель въезжает из-за правого края без горизонтальной прокрутки. */}
      <div className="flex min-h-dvh overflow-x-clip">
        <div className="min-w-0 flex-1">{children}</div>
        {shown && !shown.modal && <InlinePanel shown={shown} onClose={close} />}
      </div>
      {shown?.modal && <ModalPanel shown={shown} onClose={close} />}
    </InspectorContext.Provider>
  );
}

function InlinePanel({ shown, onClose }: { shown: Shown; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  const { version, focusRequest } = shown;
  // Фокус на каждое открытие: в отмеченное поле содержимого или в панель — тогда чтение
  // с экрана называет её заголовок.
  // biome-ignore lint/correctness/useExhaustiveDependencies: новое открытие — новый `version` или `focusRequest`
  useEffect(() => {
    const panel = ref.current;
    if (panel) {
      (initialFocus<HTMLElement>(panel) ?? panel).focus({ preventScroll: true });
    }
  }, [version, focusRequest]);
  return (
    <aside
      ref={ref}
      tabIndex={-1}
      aria-labelledby={titleId}
      className={`sticky top-0 flex h-dvh w-80 shrink-0 flex-col bg-surface outline-none ${MOTION}`}
    >
      <PanelBody shown={shown} titleId={titleId} onClose={onClose} />
    </aside>
  );
}

/**
 * Над модальным листом всё вне листа неактивно: панель — модальное окно в верхнем слое.
 * Esc, «Закрыть» и клик вне панели закрывают окно штатно (`dialog.close()`): лист снова
 * активен, и событие `close` возвращает фокус на кнопку в нём. Если убрать окно сразу,
 * фокус уйдёт на `body`: пока окно открыто, кнопка в листе неактивна.
 */
function ModalPanel({ shown, onClose }: { shown: Shown; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
    }
  }, []);
  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: Esc закрывает <dialog> штатно, клик — только вне панели
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && ref.current.close()}
      className={`fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-80 max-w-none border-0 bg-surface p-0 text-ink backdrop:bg-transparent ${MOTION}`}
    >
      <div className="flex h-full flex-col">
        <PanelBody shown={shown} titleId={titleId} onClose={() => ref.current?.close()} />
      </div>
    </dialog>
  );
}

function PanelBody({
  shown,
  titleId,
  onClose,
}: {
  shown: Shown;
  titleId: string;
  onClose: () => void;
}) {
  return (
    <>
      <header className="flex shrink-0 items-center gap-2 px-5 pt-5 pb-4">
        <h2 id={titleId} className="min-w-0 flex-1 text-xl leading-[26px] font-semibold text-ink">
          {shown.title}
        </h2>
        <Button variant="quiet" onClick={onClose}>
          Закрыть
        </Button>
      </header>
      <div
        key={shown.version}
        className="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 pb-5 animate-fade-in"
      >
        {shown.body}
      </div>
    </>
  );
}
