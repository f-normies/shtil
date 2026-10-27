import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/_doctor/alerts/")({
  component: () => (
    <p className="flex-1 px-8 py-6 text-ink-muted">Выберите сигнал в списке слева.</p>
  ),
});
