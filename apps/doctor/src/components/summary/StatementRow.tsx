import { StatusIcon } from "@compass/ui";
import { Link } from "@tanstack/react-router";
import { Bell, ChartLine, FileText, type LucideIcon, MessageSquare } from "lucide-react";
import type { LevelView, SourceLink, StatementIcon, StatementView } from "../../card/summary";

const LEVEL_TONE: Record<LevelView["tone"], string> = {
  mandatory: "text-danger",
  desirable: "text-warning",
};

const LUCIDE: Partial<Record<StatementIcon, LucideIcon>> = {
  scale: ChartLine,
  signal: Bell,
  diary: FileText,
  messages: MessageSquare,
};

/**
 * Значок утверждения. Уровень сигнала — знаком «Обязательно» или «Желательно».
 * `decorative` — слово уровня уже написано рядом, повторять его для чтения с экрана не нужно.
 */
export function StatementIconView({
  icon,
  decorative = false,
}: {
  icon: StatementIcon;
  decorative?: boolean;
}) {
  if (icon === "mandatory" || icon === "desirable") {
    return (
      <StatusIcon
        status={icon === "mandatory" ? "required" : "advised"}
        decorative={decorative}
        className="size-5 shrink-0"
      />
    );
  }
  const Icon = LUCIDE[icon] ?? Bell;
  return <Icon aria-hidden className="size-5 shrink-0 text-ink" strokeWidth={1.75} />;
}

/**
 * Утверждение сводки (кадр 05б, «Строка события»): текст, факты под ним, даты и ссылки
 * на разделы карточки. Разделов дневника (#31) и чата в карточке ещё нет: подпись без ссылки.
 */
export function StatementRow({
  statement,
  patientId,
  last,
}: {
  statement: StatementView;
  patientId: string;
  last: boolean;
}) {
  return (
    <li className="relative flex items-start gap-3 px-4 py-3">
      <StatementIconView icon={statement.icon} decorative={statement.level !== null} />
      <div className="min-w-0 flex-1 break-words">
        <p className="text-[15px] leading-[22px] text-ink">{statement.text}</p>
        <Details statement={statement} />
      </div>
      {statement.dates && (
        <p className="shrink-0 whitespace-nowrap text-[13px] leading-[18px] text-ink-subtle">
          {statement.dates}
        </p>
      )}
      {statement.links.length > 0 && (
        <ul className="flex shrink-0 flex-col items-end gap-1">
          {statement.links.map((link) => (
            <li key={link.label}>
              <SourceLinkView link={link} patientId={patientId} />
            </li>
          ))}
        </ul>
      )}
      {!last && <div aria-hidden className="absolute right-0 bottom-0 left-12 h-px bg-line" />}
    </li>
  );
}

/** Факты под утверждением; у сигнала первым идёт слово уровня из поля `level`. */
export function Details({ statement, prefix }: { statement: StatementView; prefix?: string }) {
  const { level } = statement;
  const text = [prefix, statement.details].filter(Boolean).join(" · ");
  if (!level && !text) {
    return null;
  }
  return (
    <p className="text-[13px] leading-[18px] text-ink-muted">
      {level && <span className={`font-semibold ${LEVEL_TONE[level.tone]}`}>{level.word}</span>}
      {level && text && " · "}
      {text}
    </p>
  );
}

const LINK =
  "whitespace-nowrap rounded-sm text-sm font-semibold text-primary-text hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

function SourceLinkView({ link, patientId }: { link: SourceLink; patientId: string }) {
  if (link.target === "scales") {
    // Раздел «Шкалы» карточки: графики всех шкал пациента (спека #13, раздел 10.4).
    return (
      <Link className={LINK} to="/patients/$patientId/scales" params={{ patientId }}>
        {link.label}
      </Link>
    );
  }
  if (link.target === "signals") {
    return (
      <Link className={LINK} to="/alerts" search={{ patient: patientId }}>
        {link.label}
      </Link>
    );
  }
  // Обычное начертание: подпись не должна выглядеть ссылкой.
  return <span className="whitespace-nowrap text-sm text-ink-muted">{link.label}</span>;
}
