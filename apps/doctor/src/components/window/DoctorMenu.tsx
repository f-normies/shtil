import { Avatar, Brand, Counter, type CounterTone, StatusIcon } from "@compass/ui";
import { Link, useLocation } from "@tanstack/react-router";
import { Bell, LogOut, Users } from "lucide-react";
import type { ReactNode } from "react";
import { initials, shortName } from "../../card/names";
import { useOpenAlerts, usePatients, useProposalSummary } from "../../card/queries";

const ITEM =
  "flex h-9 items-center gap-3 rounded-lg px-3 text-sm font-medium text-ink hover:bg-surface";
const SELECTED = "bg-surface text-primary-text shadow-[0_1px_2px_rgb(16_26_32/6%)]";

/**
 * Меню окна врача 232 px (спека #13, раздел 10.1; компонент «Врач v4/Меню»): «Пациенты»,
 * «Сигналы», «Предложения» со счётчиками, внизу врач и «Выйти». Счётчик сигналов окрашен
 * по самому высокому открытому уровню (раздел 10.8).
 */
export function DoctorMenu({ doctorName, onLogout }: { doctorName: string; onLogout: () => void }) {
  const { pathname } = useLocation();
  const patients = usePatients();
  const alerts = useOpenAlerts();
  const summary = useProposalSummary();
  const pending = summary.data?.pending;
  const open = alerts.data ?? [];
  const tone: CounterTone = open.some((a) => a.level === "mandatory")
    ? "required"
    : open.length > 0
      ? "advised"
      : "neutral";
  const onSignals = pathname.startsWith("/alerts");
  const onProposals = pathname.startsWith("/proposals");
  const onPatients = !onSignals && !onProposals;

  return (
    <nav aria-label="Разделы кабинета" className="w-[232px] shrink-0 bg-surface-sunken">
      <div className="flex h-full flex-col px-3 pt-5 pb-4">
        <Brand persona="doctor" className="px-2 pb-6" />
        <ul className="flex flex-col gap-1">
          <li>
            <Link
              to="/"
              className={`${ITEM} ${onPatients ? SELECTED : ""}`}
              aria-current={onPatients ? "page" : undefined}
            >
              <Users aria-hidden className={`size-5 ${onPatients ? "text-primary" : ""}`} />
              <ItemText count={patients.data?.length}>Пациенты</ItemText>
            </Link>
          </li>
          <li>
            <Link
              to="/alerts"
              search={{}}
              className={`${ITEM} ${onSignals ? SELECTED : ""}`}
              aria-current={onSignals ? "page" : undefined}
            >
              <Bell aria-hidden className={`size-5 ${onSignals ? "text-primary" : ""}`} />
              <ItemText count={alerts.data?.length} tone={tone} level>
                Сигналы
              </ItemText>
            </Link>
          </li>
          <li>
            <Link
              to="/proposals"
              search={{}}
              className={`${ITEM} ${onProposals ? SELECTED : ""}`}
              aria-current={onProposals ? "page" : undefined}
            >
              <StatusIcon status="proposed" decorative />
              <ItemText count={pending}>
                Предложения
                {pending !== undefined && pending > 0 && (
                  <span className="sr-only"> ждут решения: </span>
                )}
              </ItemText>
            </Link>
          </li>
        </ul>
        <div className="mt-auto flex flex-col gap-3 px-2">
          <div className="flex items-center gap-3">
            <Avatar initials={initials(doctorName)} />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{shortName(doctorName)}</p>
              <p className="text-xs text-ink-muted">Врач</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onLogout}
            className="flex h-9 items-center gap-3 text-sm text-ink hover:text-primary-text"
          >
            <LogOut aria-hidden className="size-5" />
            Выйти
          </button>
        </div>
      </div>
    </nav>
  );
}

/**
 * Подпись пункта и счётчик. У счётчика сигналов уровень передан не только цветом:
 * значком и словом для экранных читалок (`level`).
 */
function ItemText({
  children,
  count,
  tone = "neutral",
  level = false,
}: {
  children: ReactNode;
  count: number | undefined;
  tone?: CounterTone;
  level?: boolean;
}) {
  const marked = level && (tone === "required" || tone === "advised");
  return (
    <>
      <span className="flex-1">{children}</span>
      {marked && (
        <StatusIcon
          status={tone}
          label={tone === "required" ? "есть «Обязательно»" : "есть «Желательно»"}
          className="size-4"
        />
      )}
      {count !== undefined && count > 0 && <Counter value={count} tone={tone} />}
    </>
  );
}
