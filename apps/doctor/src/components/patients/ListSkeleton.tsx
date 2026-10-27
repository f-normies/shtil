const ROW_KEYS = ["a", "b", "c", "d", "e", "f", "g", "h"];

/**
 * Скелетон списка на время загрузки (кадр 02): 8 строк, виден, если ответа нет дольше
 * 300 мс (задержка — в CSS `list-skeleton`), чтобы при быстром ответе не мигать.
 */
export function ListSkeleton({ label }: { label: string }) {
  return (
    <div role="status" className="list-skeleton flex flex-col gap-1 px-3">
      <span className="sr-only">{label}</span>
      <div aria-hidden="true" className="flex flex-col gap-1">
        {ROW_KEYS.map((key) => (
          <div key={key} data-part="skeleton-row" className="flex animate-pulse gap-3 py-2.5">
            <span className="mt-0.5 size-5 shrink-0 rounded-full bg-surface-sunken" />
            <span className="flex min-w-0 flex-1 flex-col gap-1.5">
              <span className="h-4 w-3/4 rounded bg-surface-sunken" />
              <span className="h-3 w-1/2 rounded bg-surface-sunken" />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
