import type { Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { Trash2 } from "lucide-react";
import { useId, useState } from "react";
import {
  confirmBody,
  editHint,
  editItems,
  removeItem,
  statementView,
  updateItem,
} from "../../card/summary";
import { Sheet } from "../details/Sheet";
import { Details, StatementIconView } from "./StatementRow";

type Summary = Schemas["VisitSummaryOut"];

/** Предел текста утверждения на сервере (`ConfirmSummaryIn`, спека #38, 3.5). */
const MAX_TEXT = 500;

/**
 * Лист «Изменить сводку» (спека #38, раздел 3.6) на общем листе «Врач v4/Лист». Поле на
 * каждое утверждение и удаление; добавить утверждение нельзя: без фактов его не проверить.
 * Под полем — факты, на которые утверждение опирается. Esc и клик по затемнению закрывают
 * лист без подтверждения.
 */
export function SummarySheet({
  summary,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  summary: Summary;
  busy: boolean;
  error: string | null;
  onConfirm: (body: Schemas["ConfirmSummaryIn"]) => void;
  onClose: () => void;
}) {
  const id = useId();
  const [items, setItems] = useState(() => editItems(summary.statements));
  const hint = editHint(items);

  return (
    <Sheet
      title="Изменить сводку"
      description="Поправьте текст или удалите утверждение. Добавить новое нельзя: каждое утверждение опирается на данные пациента."
      onClose={onClose}
    >
      {(close) => (
        <form
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!hint) {
              onConfirm(confirmBody(items));
            }
          }}
        >
          {items.length > 0 && (
            <ol className="flex flex-col gap-4">
              {items.map((item, index) => {
                const view = statementView(item, summary.facts);
                const label = `Утверждение ${index + 1}`;
                return (
                  <li key={item.key} className="flex gap-3">
                    <span className="pt-8">
                      <StatementIconView icon={view.icon} decorative={view.level !== null} />
                    </span>
                    <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                      <label className="flex flex-col gap-1.5 text-sm font-medium">
                        {label}
                        <textarea
                          className="max-h-40 min-h-11 rounded-md border border-line px-3 py-2 text-base font-normal [field-sizing:content] aria-invalid:border-danger"
                          maxLength={MAX_TEXT}
                          aria-invalid={!item.text.trim()}
                          value={item.text}
                          onChange={(e) => setItems(updateItem(items, item.key, e.target.value))}
                        />
                      </label>
                      <Details statement={view} prefix={view.dates} />
                    </div>
                    <span className="pt-7">
                      <Button
                        type="button"
                        variant="quiet"
                        aria-label={`Удалить: ${label.toLowerCase()}`}
                        icon={<Trash2 strokeWidth={1.75} />}
                        onClick={() => setItems(removeItem(items, item.key))}
                      >
                        Удалить
                      </Button>
                    </span>
                  </li>
                );
              })}
            </ol>
          )}

          {error && <Alert tone="error">{error}</Alert>}

          <div className="flex flex-wrap items-center justify-end gap-2">
            <p id={`${id}-hint`} role="status" className="mr-auto text-sm text-ink-muted">
              {hint ?? ""}
            </p>
            <Button type="button" variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button type="submit" disabled={busy || hint !== null} aria-describedby={`${id}-hint`}>
              Подтвердить сводку
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}
