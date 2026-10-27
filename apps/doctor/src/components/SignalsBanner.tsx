import { StatusIcon } from "@compass/ui";
import { Link, useMatches } from "@tanstack/react-router";
import { WifiOff } from "lucide-react";
import { useState } from "react";
import { bannerNote, deputyNote } from "../card/escalation";
import { askNotifications, canAskNotifications } from "../card/notify";
import { usePatientNames } from "../card/patientNames";
import { useOpenAlerts } from "../card/queries";
import { bannerRows, bannerSignals } from "../card/signals";
import { useRealtimeState } from "../card/useRealtime";

/**
 * Вверху панели подробностей на всех экранах (спека #13, разделы 4.5 и 10.8): строка потери
 * связи и плашка открытых сигналов «Обязательно». Без плашки отметка о доставке была бы
 * ложной: сигнал «дошёл», а врач его не видит. Вид — по правилу предупреждений v4: значок,
 * слово, тон без рамки. Эскалация (спека #56): у сигнала — сколько не взят или кому передан,
 * у сигнала заместителя — чей пациент; ссылка «Включить уведомления», пока нет разрешения.
 */
export function SignalsBanner() {
  const { connected, alertEvents } = useRealtimeState();
  // Самый глубокий маршрут — открытая страница: на обзоре пациента его сигналы уже видны.
  const page = useMatches({ select: (matches) => matches.at(-1) });
  const open = useOpenAlerts();
  const [hiddenAt, setHiddenAt] = useState<number | null>(null);
  const signals = bannerSignals(open.data ?? [], page);
  const names = usePatientNames(signals);
  const hidden = hiddenAt !== null && hiddenAt >= alertEvents;
  const [ask, setAsk] = useState(canAskNotifications);
  // Кадр 12: по строке на сигнал, больше трёх — «Ещё N» со ссылкой на «Сигналы».
  const rows = bannerRows(signals);

  return (
    <>
      {!connected && (
        <p
          role="status"
          className="flex items-center gap-2 bg-surface-sunken px-8 py-2 text-sm text-ink"
        >
          <WifiOff aria-hidden className="size-4 text-ink-muted" />
          Нет связи с сервером, новые сигналы могут прийти с задержкой
        </p>
      )}
      {signals.length > 0 && !hidden && (
        <section
          role="alert"
          aria-label="Сигналы «Обязательно»"
          className="flex items-start gap-4 bg-required-soft px-8 py-3"
        >
          <ul className="flex min-w-0 flex-1 flex-col gap-1.5">
            {rows.shown.map((s) => (
              <li key={s.id} className="flex min-w-0 items-center gap-x-3 text-sm">
                <span className="flex shrink-0 items-center gap-2 font-semibold text-required">
                  <StatusIcon status="required" decorative />
                  Обязательно
                </span>
                <span className="min-w-0 truncate text-ink" title={s.title}>
                  {names.get(s.patient_id) ?? "Пациент"} — {s.title}
                </span>
                {(deputyNote(s) ?? bannerNote(s)) && (
                  <span className="shrink-0 font-semibold text-ink">
                    {deputyNote(s) ?? bannerNote(s)}
                  </span>
                )}
                <Link
                  className="shrink-0 font-semibold text-primary-text hover:underline"
                  to="/alerts/$alertId"
                  params={{ alertId: s.id }}
                  search={{}}
                >
                  Открыть сигнал
                </Link>
              </li>
            ))}
            {rows.rest > 0 && (
              <li className="text-sm">
                <Link className="font-semibold text-primary-text hover:underline" to="/alerts">
                  Ещё {rows.rest} — все сигналы
                </Link>
              </li>
            )}
          </ul>
          {ask && (
            <button
              type="button"
              className="text-sm font-semibold text-primary-text hover:underline"
              onClick={() => void askNotifications().finally(() => setAsk(canAskNotifications()))}
            >
              Включить уведомления
            </button>
          )}
          <button
            type="button"
            className="text-sm text-ink-muted hover:text-ink"
            onClick={() => setHiddenAt(alertEvents)}
          >
            Скрыть
          </button>
        </section>
      )}
    </>
  );
}
