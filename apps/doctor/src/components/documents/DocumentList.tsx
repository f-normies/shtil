import { $api, type Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, LoaderCircle, RotateCw } from "lucide-react";
import type { KeyboardEvent } from "react";
import { documentMeta, retryErrorText, statusView } from "../../card/documents";
import { refreshDocuments } from "../../card/queries";
import { DIVIDER, Group } from "./Group";

type DocumentOut = Schemas["DocumentOut"];

/**
 * Документы пациента, новые сверху (кадр 07в). Выбранный подсвечен, ниже — его факты.
 * ↑↓ переводят фокус между документами, Enter выбирает.
 */
export function DocumentList({
  documents,
  selectedId,
  onSelect,
  now,
}: {
  documents: DocumentOut[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  now: Date;
}) {
  const moveFocus = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") {
      return;
    }
    const buttons = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("[data-document]")];
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = buttons[index + (e.key === "ArrowDown" ? 1 : -1)];
    if (next) {
      e.preventDefault();
      next.focus();
    }
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: стрелки дополняют обычные кнопки строк.
    <div onKeyDown={moveFocus}>
      <Group id="documents-list" title="Документы">
        {documents.map((d) => (
          <DocumentRow
            key={d.id}
            document={d}
            selected={d.id === selectedId}
            onSelect={() => onSelect(d.id)}
            now={now}
          />
        ))}
      </Group>
    </div>
  );
}

function DocumentRow({
  document,
  selected,
  onSelect,
  now,
}: {
  document: DocumentOut;
  selected: boolean;
  onSelect: () => void;
  now: Date;
}) {
  const queryClient = useQueryClient();
  const status = statusView(document, now);
  const retry = $api.useMutation(
    "post",
    "/api/v1/patients/{patient_id}/documents/{document_id}/retry",
    { onSettled: () => refreshDocuments(queryClient) },
  );
  const ink = selected ? "text-primary-ink" : "text-ink";
  const muted = selected ? "text-primary-ink" : "text-ink-muted";

  return (
    <li className={`${DIVIDER} ${selected ? "rounded-lg bg-primary" : ""}`}>
      <button
        type="button"
        data-document
        aria-current={selected ? "true" : undefined}
        onClick={onSelect}
        className={`flex w-full gap-3 px-4 py-3 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 ${selected ? "rounded-lg focus-visible:outline-primary-ink" : "hover:bg-canvas focus-visible:outline-primary"}`}
      >
        <StatusIcon tone={status.tone} selected={selected} />
        <span className="min-w-0 flex-1">
          <span className={`block text-[15px] leading-[22px] break-words ${ink}`}>
            {document.filename}
          </span>
          <span className={`block text-[13px] leading-[18px] ${muted}`}>
            {documentMeta(document, now)}
          </span>
          {status.tone === "failed" && (
            <span
              className={`mt-1 block text-[13px] leading-[18px] font-semibold ${selected ? "text-primary-ink" : "text-danger"}`}
            >
              {status.text}
            </span>
          )}
          {status.long && (
            <span className={`mt-1 block text-[13px] leading-[18px] ${ink}`}>
              Разбор идёт дольше обычного
            </span>
          )}
        </span>
        {status.tone !== "failed" && (
          <span className={`shrink-0 text-[13px] leading-[18px] whitespace-nowrap ${muted}`}>
            {status.text}
          </span>
        )}
      </button>
      {status.retry && (
        <div className="flex flex-wrap items-center gap-3 pb-3 pl-12 pr-4">
          <Button
            variant="secondary"
            disabled={retry.isPending}
            onClick={() =>
              retry.mutate({
                params: { path: { patient_id: document.patient_id, document_id: document.id } },
              })
            }
          >
            <RotateCw aria-hidden className="size-4" />
            Повторить
          </Button>
          {retry.isError && (
            <p
              role="alert"
              className={`text-[13px] leading-[18px] ${selected ? "text-primary-ink" : "text-danger"}`}
            >
              Ошибка: {retryErrorText(retry.error)}.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function StatusIcon({
  tone,
  selected,
}: {
  tone: "work" | "done" | "failed";
  selected: boolean;
}) {
  const color = selected
    ? "text-primary-ink"
    : tone === "failed"
      ? "text-danger"
      : "text-ink-muted";
  const className = `mt-0.5 size-5 shrink-0 ${color}`;
  if (tone === "done") {
    return <Check aria-hidden className={className} />;
  }
  if (tone === "failed") {
    return <CircleAlert aria-hidden className={className} />;
  }
  return <LoaderCircle aria-hidden className={`${className} motion-safe:animate-spin`} />;
}
