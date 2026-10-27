import type { Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useState } from "react";
import { formatDateTime } from "../../card/format";
import { useAdverseEventDraft } from "../../card/queries";
import { reactionText, reportLevel } from "../../card/therapy";
import { LEVEL_BOX, LevelBadge } from "../card/LevelBadge";

type ReactionReport = Schemas["ReactionReportOut"];

/**
 * Нежелательные реакции, которые отметил пациент, и черновик извещения (спека #29, раздел 7).
 * Уровень отметки — иконкой, словом и цветом, как у сигнала.
 */
export function Reactions({
  prescriptionId,
  reports,
  canDraft,
}: {
  prescriptionId: string;
  reports: ReactionReport[];
  canDraft: boolean;
}) {
  if (reports.length === 0) {
    return null;
  }
  return (
    <section className="flex flex-col gap-2">
      <h4 className="font-medium text-ink">Нежелательные реакции со слов пациента</h4>
      <ul className="flex flex-col gap-2">
        {reports.map((r) => (
          <li
            key={r.id}
            className={`rounded-md border px-3 py-2 [overflow-wrap:anywhere] ${LEVEL_BOX[reportLevel(r)]}`}
          >
            <p className="flex flex-wrap items-center gap-2">
              <LevelBadge level={reportLevel(r)} />
              <span className="text-ink-muted">{formatDateTime(r.reported_at)}</span>
            </p>
            <p className="mt-1 text-ink">{r.reactions.map(reactionText).join("; ")}</p>
            {r.note && <p className="mt-1 text-ink">Пациент пишет: «{r.note}»</p>}
          </li>
        ))}
      </ul>
      {canDraft && <DraftReport prescriptionId={prescriptionId} />}
    </section>
  );
}

function DraftReport({ prescriptionId }: { prescriptionId: string }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<"done" | "failed" | null>(null);
  const draft = useAdverseEventDraft(prescriptionId, open);

  if (!open) {
    return (
      <div>
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Черновик извещения
        </Button>
      </div>
    );
  }
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  };
  return (
    <div className="flex flex-col gap-2">
      <p className="text-ink-muted">
        Извещение о нежелательной реакции отправляет врач. Тяжесть и исход заполните сами.
      </p>
      {draft.isPending && <p className="text-ink-muted">Собираем черновик…</p>}
      {draft.isError && (
        <Alert tone="error">Не удалось собрать черновик. Попробуйте ещё раз.</Alert>
      )}
      {draft.data && (
        <>
          <pre className="whitespace-pre-wrap [overflow-wrap:anywhere] rounded-md border border-line bg-canvas p-3 font-sans text-ink">
            {draft.data.text}
          </pre>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void copy(draft.data.text)}>Скопировать</Button>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Скрыть
            </Button>
            <span role="status" className="text-ink-muted">
              {copied === "done" && "Скопировано"}
              {copied === "failed" && "Не удалось скопировать: выделите текст вручную"}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
