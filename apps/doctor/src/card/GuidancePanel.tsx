/**
 * Панель «Варианты по клиническим рекомендациям» в «Терапии» (спека #107, раздел 6;
 * кадр «Врач v4 · 10б»). Это справка: назначить через систему можно только препарат
 * справочника, через предложение выше.
 */
import { $api } from "@compass/api-client";
import { Button, Panel } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import {
  BookX,
  CircleAlert,
  ClipboardPlus,
  Clock,
  History,
  Info,
  LoaderCircle,
  type LucideIcon,
  SearchX,
} from "lucide-react";
import type { ReactNode } from "react";
import { ClaimItem } from "../components/knowledge/ClaimItem";
import { useOpenSource } from "../components/source/SourceInspector";
import {
  type ClaimGroup,
  computedText,
  type GuidanceAction,
  type GuidanceKind,
  type GuidanceView,
  groupClaims,
  guidanceCaption,
  guidanceView,
  type Notice,
  type NoticeTone,
  sourceText,
} from "./guidance";
import { refreshGuidance, useGuidance } from "./queries";
import { claimBasis, guidanceApplicability } from "./sources";

const RETRY = "/api/v1/patients/{patient_id}/therapy/guidance/retry";
const ICON = "size-5 shrink-0";
const STROKE = 1.75;

/** Статус — значком и словом, цвет только дополняет их (CLAUDE.md, «Интерфейс»). */
const TONES: Record<NoticeTone, { Icon: LucideIcon; box: string; icon?: string }> = {
  info: { Icon: Info, box: "bg-canvas text-ink" },
  progress: { Icon: LoaderCircle, box: "bg-canvas text-ink", icon: "motion-safe:animate-spin" },
  waiting: { Icon: Clock, box: "bg-canvas text-ink" },
  warning: { Icon: History, box: "bg-warning-soft text-warning" },
  error: { Icon: CircleAlert, box: "bg-danger-soft text-danger" },
};

/** Свой значок у пометок без расчёта: значок «инфо» уже стоит у подписи. */
const KIND_ICONS: Partial<Record<GuidanceKind, LucideIcon>> = {
  "no-diagnoses": ClipboardPlus,
  empty: SearchX,
  "no-guidelines": BookX,
};

export function GuidancePanel({ patientId }: { patientId: string }) {
  const queryClient = useQueryClient();
  const { query, slow, refresh } = useGuidance(patientId);
  const retry = $api.useMutation("post", RETRY, {
    onSettled: () => refreshGuidance(queryClient),
  });
  const guidance = query.data;
  const view = guidance ? guidanceView(guidance, slow) : null;
  // Применимость — профиль показанного ответа, при `stale` прежний (спека #106, 3.6).
  const applicability = guidanceApplicability(guidance?.profile ?? null, guidance?.stale ?? false);
  const act = (action: GuidanceAction) =>
    action === "retry" ? retry.mutate({ params: { path: { patient_id: patientId } } }) : refresh();

  return (
    <Panel>
      {/* Колонка текста — до 768 px: длинные строки цитат иначе трудно читать. */}
      <div className="flex max-w-3xl flex-col">
        <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="text-lg font-semibold text-ink">Варианты по клиническим рекомендациям</h2>
          {guidance?.computed_at && (
            <p className="text-[13px] leading-[18px] text-ink-muted">
              {computedText(guidance.computed_at)}
            </p>
          )}
        </header>
        {query.isPending && <p className="text-sm text-ink-muted">Загрузка…</p>}
        {query.isError && <p className="text-sm text-danger">Не удалось загрузить справку.</p>}
        {/* Область для чтения с экрана есть всегда: смена статуса озвучивается. */}
        <div role="status" aria-live="polite">
          {view?.notice && (
            <NoticeBox
              notice={view.notice}
              icon={KIND_ICONS[view.kind]}
              busy={retry.isPending || query.isFetching}
              failed={retry.isError && view.notice.action === "retry"}
              onAction={act}
            />
          )}
        </div>
        {guidance && view && (
          <>
            <Row icon={Info}>
              <p className="text-[13px] leading-[18px] text-ink-muted">
                {guidanceCaption(guidance.catalog_drugs)}
              </p>
            </Row>
            <Body
              view={view}
              guidelineIds={guidance.guidelines.map((g) => g.id)}
              applicability={applicability}
            />
          </>
        )}
      </div>
    </Panel>
  );
}

function Row({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex gap-3 py-3">
      <Icon aria-hidden className={`${ICON} text-ink`} strokeWidth={STROKE} />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** Волосяная линия от колонки текста: значок 20 px и отступ 12 px, как в кадре. */
function Divider() {
  return <div aria-hidden className="ml-8 h-px bg-line" />;
}

function NoticeBox({
  notice,
  icon: KindIcon,
  busy,
  failed,
  onAction,
}: {
  notice: Notice;
  icon: LucideIcon | undefined;
  busy: boolean;
  failed: boolean;
  onAction: (action: GuidanceAction) => void;
}) {
  const { Icon: ToneIcon, box, icon = "" } = TONES[notice.tone];
  const Icon = KindIcon ?? ToneIcon;
  const { action } = notice;
  return (
    <div
      className={`mb-1 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md px-3 py-2.5 ${box}`}
    >
      <Icon aria-hidden className={`${ICON} ${icon}`} strokeWidth={STROKE} />
      <p className="min-w-0 flex-1 text-sm font-medium">{notice.text}</p>
      {action && (
        <Button variant="secondary" disabled={busy} onClick={() => onAction(action)}>
          {action === "retry" ? "Повторить" : "Обновить"}
        </Button>
      )}
      {failed && (
        <p className="basis-full pl-8 text-sm">Не удалось отправить запрос. Попробуйте ещё раз.</p>
      )}
    </div>
  );
}

function Body({
  view,
  guidelineIds,
  applicability,
}: {
  view: GuidanceView;
  guidelineIds: string[];
  applicability: string[];
}) {
  if (view.skeleton) {
    return <Skeleton />;
  }
  if (view.claims.length === 0) {
    return null;
  }
  return (
    <ul>
      {groupClaims(view.claims).map((group) => (
        <li key={group.key}>
          <Divider />
          <GuidanceClaim group={group} guidelineIds={guidelineIds} applicability={applicability} />
        </li>
      ))}
    </ul>
  );
}

function GuidanceClaim({
  group: { claim, statements },
  guidelineIds,
  applicability,
}: {
  group: ClaimGroup;
  guidelineIds: string[];
  applicability: string[];
}) {
  const openSource = useOpenSource();
  // Цитата открывает фрагмент в инспекторе источника (спека #106, 3.9).
  return (
    <ClaimItem
      claim={claim}
      statements={statements}
      source={sourceText(claim.fragment, guidelineIds)}
      action="открыть источник"
      onOpen={(trigger) => openSource(claimBasis(claim, applicability), trigger)}
    />
  );
}

function Skeleton() {
  return (
    <div aria-hidden className="flex flex-col">
      {[0, 1, 2].map((row) => (
        <div key={row}>
          <Divider />
          <div className="flex gap-3 py-3 motion-safe:animate-pulse">
            <div className="size-5 shrink-0 rounded bg-line" />
            <div className="flex flex-1 flex-col gap-2">
              <div className="h-4 w-3/4 rounded bg-line" />
              <div className="h-3.5 w-full rounded bg-line" />
              <div className="h-3.5 w-5/6 rounded bg-line" />
              <div className="h-3 w-1/2 rounded bg-line" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
