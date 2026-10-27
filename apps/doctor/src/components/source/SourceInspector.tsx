import { Button } from "@compass/ui";
import { ArrowUpRight, CircleAlert, History, Info } from "lucide-react";
import { Fragment, type ReactNode, useCallback } from "react";
import { useSourceView } from "../../card/queries";
import { type Basis, keyed, refKey, type SourceRef } from "../../card/sources";
import {
  blockState,
  type FragmentRow,
  type SourceBlock as SourceBlockView,
  sourceBlock,
} from "../../card/sourceView";
import { useInspector } from "../inspector/context";

/** Открыть основание в инспекторе. Фокус после закрытия вернётся на `trigger`. */
export function useOpenSource(): (basis: Basis, trigger: HTMLElement) => void {
  const { open } = useInspector();
  return useCallback(
    (basis, trigger) =>
      open({ title: "Источник", body: <SourceInspector basis={basis} /> }, trigger),
    [open],
  );
}

/**
 * Содержимое инспектора источника (спека #106, раздел 5; кадр 10): основание, применимость
 * и блок на каждую ссылку — документ, раздел, версия, издатель, ГРЛС и текст раздела.
 */
export function SourceInspector({ basis }: { basis: Basis }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] leading-[18px] text-ink-muted">{basis.heading}</p>
      {basis.applicability.length > 0 && (
        <Field label="Применимость">
          <ul className="flex flex-col gap-1">
            {keyed(basis.applicability, (line) => line).map(({ item, key }) => (
              <li key={key}>{item}</li>
            ))}
          </ul>
        </Field>
      )}
      {keyed(basis.refs, refKey).map(({ item, key }, i) => (
        <Fragment key={key}>
          {(i > 0 || basis.applicability.length > 0) && (
            <div aria-hidden className="h-px shrink-0 bg-line" />
          )}
          <SourceBlock link={item} />
        </Fragment>
      ))}
    </div>
  );
}

function SourceBlock({ link }: { link: SourceRef }) {
  const query = useSourceView(link);
  const state = blockState(query);
  if (query.data) {
    return <Ready block={sourceBlock(query.data, link)} fragmentText={Boolean(link.fragmentId)} />;
  }
  // Пока ответа нет, ссылка на документ — по снимку адреса из сигнала или проверки.
  const document = link.url && <DocumentLink url={link.url} label="Открыть документ" />;
  if (state === "loading") {
    return (
      <div className="flex flex-col gap-4">
        {document}
        <Loading />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <Field label="Документ">{link.locator ? `${link.title}, ${link.locator}` : link.title}</Field>
      <div
        role="alert"
        className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md bg-danger-soft px-3 py-2.5 text-danger"
      >
        <CircleAlert aria-hidden className="size-5 shrink-0" strokeWidth={1.75} />
        <p className="min-w-0 flex-1 text-sm font-medium">
          {state === "missing" ? "Источник не найден" : "Не удалось загрузить источник"}
        </p>
        {state === "error" && (
          <Button variant="secondary" onClick={() => void query.refetch()}>
            Повторить
          </Button>
        )}
      </div>
      {document}
    </div>
  );
}

function Ready({ block, fragmentText }: { block: SourceBlockView; fragmentText: boolean }) {
  return (
    <section aria-label={block.title} className="flex flex-col gap-4">
      <Field label="Документ">{block.title}</Field>
      {block.supplementary && (
        <p className="flex gap-2 text-[13px] leading-[18px] text-ink">
          <Info aria-hidden className="size-4 shrink-0 translate-y-px" strokeWidth={1.75} />
          Международный источник, только как дополнение
        </p>
      )}
      {block.fields.map((f) => (
        <Field key={f.label} label={f.label}>
          {f.value}
        </Field>
      ))}
      <DocumentLink url={block.link.url} label={block.link.label} />
      <div className="flex flex-col gap-2">
        <p className="text-[13px] leading-[18px] text-ink-muted">
          {fragmentText ? "Текст фрагмента" : "Текст раздела"}
        </p>
        {block.found ? (
          <ol className="flex flex-col gap-3">
            {keyed(block.fragments, (f) => f.id).map(({ item, key }) => (
              <FragmentText key={key} fragment={item} />
            ))}
          </ol>
        ) : (
          <p className="text-sm text-ink">Текст раздела не найден в базе знаний</p>
        )}
      </div>
    </section>
  );
}

/** Документ или реестр — в новой вкладке. */
function DocumentLink({ url, label }: { url: string; label: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex min-h-9 items-center gap-2 self-start rounded-md border border-line-strong bg-surface px-4 text-[15px] leading-5 font-semibold text-ink hover:bg-canvas focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      <ArrowUpRight aria-hidden className="size-5" strokeWidth={1.75} />
      {label}
      <span className="sr-only">(откроется в новой вкладке)</span>
    </a>
  );
}

function FragmentText({ fragment: f }: { fragment: FragmentRow }) {
  return (
    <li className="flex flex-col gap-1">
      {f.heading && <p className="text-xs leading-4 text-ink-muted">{f.heading}</p>}
      {f.notes.length > 0 && (
        <p className="flex items-center gap-1.5 self-start rounded-sm bg-warning-soft px-1.5 py-0.5 text-xs leading-4 font-medium text-warning">
          <History aria-hidden className="size-3.5 shrink-0" strokeWidth={1.75} />
          {f.notes.join(". ")}
        </p>
      )}
      <p className="text-sm leading-5 [overflow-wrap:anywhere] whitespace-pre-line text-ink">
        {f.parts ? (
          <>
            {f.parts.before}
            <mark className="rounded-sm bg-warning-soft text-ink">{f.parts.quote}</mark>
            {f.parts.after}
          </>
        ) : (
          f.text
        )}
      </p>
    </li>
  );
}

/** Сведение по кадру: подпись 13/18 и значение 15/22. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 [overflow-wrap:anywhere]">
      <p className="text-[13px] leading-[18px] text-ink-muted">{label}</p>
      <div className="text-[15px] leading-[22px] text-ink">{children}</div>
    </div>
  );
}

/** Строки-заглушки текста, как в кадре, пока источник загружается. */
function Loading() {
  return (
    <div role="status" className="flex flex-col gap-2 py-1">
      <span className="sr-only">Загружаем источник…</span>
      {["w-full", "w-11/12", "w-[96%]", "w-4/5", "w-1/2"].map((width) => (
        <div
          key={width}
          aria-hidden
          className={`h-2 rounded bg-surface-sunken ${width} motion-safe:animate-pulse`}
        />
      ))}
    </div>
  );
}
