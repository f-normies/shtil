import { $api } from "@compass/api-client";
import { Link } from "@tanstack/react-router";

/**
 * Карточка «Лекарства на сегодня» на главной (спека #34, раздел 7): только если сегодня есть
 * дозы. Пока дозы не загрузились или запрос не удался, карточки нет: звать отмечать нечего.
 */
export function MedicationsCard() {
  const today = $api.useQuery("get", "/api/v1/me/doses", {});
  if (!today.data || today.data.doses.length === 0) return null;
  return (
    <section
      aria-labelledby="medications-title"
      className="flex flex-col gap-2 rounded-lg border border-line bg-surface p-4"
    >
      <h2 id="medications-title" className="font-semibold">
        Лекарства на сегодня
      </h2>
      <p className="text-sm text-ink-muted">Отметьте приём: врач увидит, как идёт лечение.</p>
      <Link className="text-sm font-medium text-primary underline" to="/medications">
        Отметить приём
      </Link>
    </section>
  );
}
