import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronRight,
  CircleDotDashed,
  Info,
  TriangleAlert,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { whenText } from "../../card/documents";
import { decisionErrorText, factView, type Marker } from "../../card/facts";
import { refreshProposals, refreshTherapy } from "../../card/queries";
import { DIVIDER } from "./Group";

type Proposal = Schemas["ProposalOut"];
type DocumentOut = Schemas["DocumentOut"];

// Длинная цитата — до 6 строк и «Показать полностью» (кадр 07в). Строк без замера
// не сосчитать: длиннее этого числа знаков цитата почти наверняка не помещается.
const LONG_QUOTE = 360;

/**
 * Факт-предложение из документа (кадр 07в): строка раскрывается на месте, внутри —
 * цитата со страницей, что изменится в карточке и решение врача.
 */
export function FactRow({
  proposal,
  document,
  open,
  onToggle,
  onDecided,
  now,
}: {
  proposal: Proposal;
  document: DocumentOut;
  open: boolean;
  onToggle: () => void;
  onDecided: () => void;
  now: Date;
}) {
  const view = factView(proposal.kind, proposal.payload);
  const panelId = `fact-${proposal.id}`;

  return (
    <li className={DIVIDER}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className="flex w-full gap-3 px-4 pt-3 pb-3 text-left hover:bg-canvas focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
      >
        <span title="Предложение системы" className="mt-0.5 flex size-5 shrink-0">
          <CircleDotDashed aria-hidden className="size-5 text-primary-text" />
        </span>
        <span className="sr-only">Предложение системы:</span>
        <span className="min-w-0 flex-1">
          <span
            className={`block text-[15px] leading-[22px] text-ink ${open ? "font-semibold" : ""}`}
          >
            {view.title}
          </span>
          {view.subtitle && !(open && view.subtitleIsQuote) && (
            <span className="block text-[13px] leading-[18px] text-ink-muted">{view.subtitle}</span>
          )}
          {view.marker && <MarkerLine marker={view.marker} />}
        </span>
        {view.page !== null && (
          <span className="shrink-0 text-[13px] leading-[18px] whitespace-nowrap text-ink-muted">
            стр. {view.page}
          </span>
        )}
        {open ? (
          <ChevronDown aria-hidden className="size-4 shrink-0 text-ink-muted" />
        ) : (
          <ChevronRight aria-hidden className="size-4 shrink-0 text-ink-muted" />
        )}
      </button>
      {open && (
        <div id={panelId} className="flex flex-col gap-3.5 pr-4 pb-4 pl-12">
          <Quote text={view.quote} page={view.page} />
          <dl className="flex flex-col gap-1.5 text-sm">
            <Pair name="Источник">
              <a
                className="text-primary underline-offset-2 hover:underline"
                href={fileUrl(document, view.page)}
                target="_blank"
                rel="noopener"
              >
                {document.filename}
                {view.page !== null && `, стр. ${view.page}`}
              </a>
            </Pair>
            <Pair name="Найдено">
              Разбор документа, {whenText(document.extracted_at ?? proposal.created_at, now)}
            </Pair>
            <Pair name="Если подтвердить">{view.effect}</Pair>
          </dl>
          <Decision proposal={proposal} onDecided={onDecided} />
        </div>
      )}
    </li>
  );
}

/** Адрес оригинала; у PDF — сразу на странице цитаты. */
export function fileUrl(document: DocumentOut, page: number | null = null): string {
  const url = `/api/v1/patients/${document.patient_id}/documents/${document.id}/file`;
  return page !== null && document.content_type === "application/pdf" ? `${url}#page=${page}` : url;
}

/** Сверка с ГРЛС или «Фактор риска»: значок и слово, цвет только дополняет. */
export function MarkerLine({ marker }: { marker: Marker }) {
  const Icon = marker.tone === "ok" ? Check : marker.tone === "risk" ? Info : TriangleAlert;
  const color = {
    ok: "text-ink-muted",
    risk: "text-ink",
    warning: "font-medium text-warning",
  }[marker.tone];
  return (
    <span className={`mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] ${color}`}>
      <Icon aria-hidden className="size-4 shrink-0" />
      {marker.text}
    </span>
  );
}

function Quote({ text, page }: { text: string; page: number | null }) {
  const [full, setFull] = useState(false);
  const long = text.length > LONG_QUOTE;
  return (
    <figure className="flex flex-col gap-0.5">
      <figcaption className="text-[13px] leading-[18px] text-ink-muted">
        Цитата из документа{page !== null && `, стр. ${page}`}
      </figcaption>
      <blockquote
        className={`text-[15px] leading-[22px] text-ink ${long && !full ? "line-clamp-6" : ""}`}
      >
        «{text}»
      </blockquote>
      {long && (
        <button
          type="button"
          className="self-start text-sm font-medium text-primary underline"
          onClick={() => setFull(!full)}
        >
          {full ? "Свернуть" : "Показать полностью"}
        </button>
      )}
    </figure>
  );
}

function Pair({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-[140px] shrink-0 text-ink-muted">{name}</dt>
      <dd className="min-w-0 flex-1 text-ink">{children}</dd>
    </div>
  );
}

/** «Подтвердить» и «Отклонить с причиной». Правки факта нет (спека #30, раздел 2). */
function Decision({ proposal, onDecided }: { proposal: Proposal; onDecided: () => void }) {
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  // Подтверждённый диагноз или препарат меняет терапию: обновляем её вместе с предложениями.
  const options = {
    onSuccess: async () => {
      onDecided();
      await refreshTherapy(queryClient);
    },
  };
  const confirm = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/confirm", options);
  const reject = $api.useMutation("post", "/api/v1/proposals/{proposal_id}/reject", options);
  const params = { path: { proposal_id: proposal.id } };
  const busy = confirm.isPending || reject.isPending;
  const failure = confirm.error ?? reject.error;
  const error = failure ? decisionErrorText(failure) : null;
  const reasonId = `reason-${proposal.id}`;

  return (
    <div className="flex flex-col gap-2 pt-0.5">
      {rejecting && (
        <label htmlFor={reasonId} className="flex flex-col gap-1 text-sm text-ink">
          Причина отклонения
          <textarea
            id={reasonId}
            className="min-h-16 rounded-md border border-line px-2 py-1 text-[15px]"
            maxLength={2000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
      )}
      {error && (
        <div className="flex flex-col gap-2">
          <Alert tone="error">{error.text}.</Alert>
          {error.stale && (
            <div>
              <Button variant="secondary" onClick={() => refreshProposals(queryClient)}>
                Обновить список
              </Button>
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {rejecting ? (
          <>
            <Button
              disabled={busy || !reason.trim()}
              onClick={() => {
                confirm.reset();
                reject.mutate({ params, body: { comment: reason.trim() } });
              }}
            >
              Отклонить
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setRejecting(false)}>
              Отмена
            </Button>
          </>
        ) : (
          <>
            <Button
              disabled={busy}
              onClick={() => {
                reject.reset();
                confirm.mutate({ params, body: {} });
              }}
            >
              Подтвердить
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
              Отклонить с причиной
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
