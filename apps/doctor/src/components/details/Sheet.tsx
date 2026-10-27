import { type ReactNode, useEffect, useId, useRef } from "react";

/**
 * Лист («Врач v4/Лист»): модальное окно сверху панели подробностей, 688 px, затемнение
 * scrim. Esc и клик по затемнению закрывают; `<dialog>` сам держит фокус внутри и
 * возвращает его на кнопку, открывшую лист. Появление — проявлением 150 мс.
 */
export function Sheet({
  title,
  description,
  onClose,
  children,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: (close: () => void) => ReactNode;
}) {
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const close = () => ref.current?.close();

  useEffect(() => {
    ref.current?.showModal();
  }, []);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: Esc закрывает <dialog> штатно, клик — только по затемнению.
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && close()}
      className="mx-auto mt-6 max-h-[90dvh] w-[688px] max-w-[calc(100%-2rem)] animate-fade-in overflow-y-auto rounded-xl bg-surface p-0 text-ink shadow-xl backdrop:bg-scrim"
    >
      <div className="flex flex-col gap-4 p-6">
        <header>
          <h2 id={`${id}-title`} className="text-xl leading-[26px] font-semibold">
            {title}
          </h2>
          {description && <p className="text-[15px] text-ink-muted">{description}</p>}
        </header>
        {children(close)}
      </div>
    </dialog>
  );
}
