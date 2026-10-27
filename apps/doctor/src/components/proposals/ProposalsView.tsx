import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  decisionNotice,
  groupByPatient,
  type Outcome,
  type PatientGroup,
  positionOf,
  type QueueItem,
  relatedProposals,
  selectionAfterDecision,
} from "../../card/proposalQueue";
import { ProposalDetails } from "./ProposalDetails";
import { ProposalList } from "./ProposalList";

const LAYOUT = {
  // Экран «Предложения» (кадр 04): панель списка 360 px на всю высоту, подробности справа.
  screen: {
    root: "flex min-h-[calc(100dvh-57px)]",
    list: "flex w-[360px] shrink-0 flex-col gap-3 border-r border-line bg-surface px-2 py-4",
    details: "min-w-0 max-w-[688px] flex-1 px-8 py-6",
  },
  // Раздел карточки (кадр 07б): тот же вид внутри карточки, без групп.
  card: {
    root: "flex items-start gap-6",
    list: "flex w-[360px] shrink-0 flex-col gap-3 rounded-xl bg-surface px-2 py-3",
    details: "min-w-0 max-w-[656px] flex-1",
  },
} as const;

/**
 * Общий вид предложений (спека #96, 3.5): список слева, подробности выбранного справа.
 * Без выбора открыто первое. После решения выбор переходит к следующему того же пациента,
 * затем к следующему пациенту; решённая строка уходит проявлением за 150 мс. Фокус —
 * на выбранной строке, решение озвучивается (`role="status"`). При 409 «уже решено»
 * и 404 — то же, и сообщение словами. Если врач, пока решение сохранялось, выбрал другую
 * строку, выбор не меняется.
 */
export function ProposalsView({
  items,
  grouped,
  header,
  footer,
  loading,
  failed,
  empty,
  doctorId,
  variant,
  resetKey,
  initialId,
}: {
  items: QueueItem[];
  grouped: boolean;
  header: ReactNode;
  footer?: ReactNode;
  loading: boolean;
  failed: boolean;
  empty: string;
  doctorId: string;
  variant: keyof typeof LAYOUT;
  /** Другая вкладка статуса — другой список: выбор и ушедшие строки начинаются заново. */
  resetKey: string;
  /** Предложение, открытое при входе: «Разобрать» в разделе «Приём» (#34). */
  initialId?: string;
}) {
  const [chosen, setChosen] = useState<string | null>(initialId ?? null);
  const [leaving, setLeaving] = useState<ReadonlySet<string>>(new Set());
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [notice, setNotice] = useState<{ text: string; visible: boolean } | null>(null);
  // Пункт, выбранный автоматически после решения: его кнопки решения включаются не сразу.
  const [guarded, setGuarded] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  const [list, setList] = useState(resetKey);
  if (list !== resetKey) {
    // Состояние по прошлой вкладке: React перерисует сразу, без эффекта. Шапку со строкой
    // поиска и переключателем не пересоздаём, иначе фокус ушёл бы из них.
    setList(resetKey);
    setChosen(null);
    setLeaving(new Set());
    setGone(new Set());
    setNotice(null);
    setGuarded(null);
  }
  const details = useRef<HTMLDivElement>(null);
  const column = useRef<HTMLDivElement>(null);
  const status = useRef<HTMLParagraphElement>(null);
  const layout = LAYOUT[variant];
  const now = new Date();

  // Ушедшие строки скрыты, пока список не обновится; уходящие ещё видны, но не выбираются.
  const shown = items.filter((i) => !gone.has(i.id));
  const active = shown.filter((i) => !leaving.has(i.id));
  const groupsOf = (list: QueueItem[]): PatientGroup[] =>
    grouped ? groupByPatient(list) : [{ patientId: "", name: "", items: list }];
  const activeGroups = groupsOf(active);
  const selected = active.find((i) => i.id === chosen) ?? active[0] ?? null;
  // Ответ на решение приходит и после ухода с пункта: колбэк мутации видит прежний рендер.
  // Выбор и группы на момент ответа берутся отсюда.
  const latest = useRef({ selectedId: selected?.id ?? null, groups: activeGroups, resetKey });
  useLayoutEffect(() => {
    latest.current = { selectedId: selected?.id ?? null, groups: activeGroups, resetKey };
  });
  useEffect(() => {
    if (focusRequest === 0) {
      return;
    }
    // После решения фокус — на выбранной строке; список пуст — на сообщении о решении.
    const row = column.current?.querySelector<HTMLElement>('button[aria-current="true"]');
    (row ?? status.current)?.focus();
  }, [focusRequest]);

  const select = (id: string) => {
    setChosen(id);
    setNotice(null);
    setGuarded(null);
  };
  const decided = (id: string, outcome: Outcome, title: string) => {
    // Ответ из прежней вкладки обновит запросы в Decision. Здесь он не должен
    // скрывать предложение, которое теперь принадлежит истории решений.
    if (latest.current.resetKey !== resetKey) {
      return;
    }
    const { selectedId, groups } = latest.current;
    const after = selectionAfterDecision(groups, selectedId, id);
    setLeaving((prev) => new Set(prev).add(id));
    setNotice(decisionNotice(outcome, title, after.moved));
    if (after.moved) {
      setChosen(after.selectedId);
      setGuarded(after.selectedId);
      setFocusRequest((n) => n + 1);
    }
  };

  return (
    <div className={layout.root}>
      <div ref={column} className={layout.list}>
        {header}
        {loading && <p className="px-2 text-sm text-ink-muted">Загружаем предложения…</p>}
        {failed && (
          <p role="alert" className="px-2 text-sm text-danger">
            Не удалось загрузить предложения. Проверьте связь и обновите страницу.
          </p>
        )}
        {!loading && !failed && active.length === 0 && (
          <p className="px-3 py-2 text-[15px] text-ink-muted">{empty}</p>
        )}
        <ProposalList
          groups={groupsOf(shown)}
          grouped={grouped}
          selectedId={selected?.id ?? null}
          leaving={leaving}
          onSelect={select}
          onLeft={(id) => setGone((prev) => new Set(prev).add(id))}
          onEnter={() =>
            details.current?.querySelector<HTMLElement>("[data-primary-action]")?.focus()
          }
          now={now}
        />
        {footer}
      </div>
      <div ref={details} className={layout.details}>
        <p
          ref={status}
          role="status"
          tabIndex={-1}
          className={
            notice?.visible ? "mb-4 text-[15px] font-medium text-ink outline-none" : "sr-only"
          }
        >
          {notice?.text}
        </p>
        {selected && (
          <ProposalDetails
            key={selected.id}
            item={selected}
            position={positionOf(activeGroups, selected.id)}
            related={relatedProposals(selected, active)}
            showPatient={grouped}
            doctorId={doctorId}
            guard={selected.id === guarded}
            onDecided={decided}
            onOpen={select}
            now={now}
          />
        )}
      </div>
    </div>
  );
}
