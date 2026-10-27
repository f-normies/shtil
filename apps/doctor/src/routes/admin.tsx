import { $api } from "@compass/api-client";
import { AppHeader, Button, StatusIcon } from "@compass/ui";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { DeputiesSection } from "../admin/DeputiesSection";
import { ESCALATIONS, EscalationsSection } from "../admin/EscalationsSection";
import { bannerText } from "../admin/escalations";
import { useAdminEvents } from "../admin/useAdminEvents";
import { currentUser, homeLocation } from "../session";

export const Route = createFileRoute("/admin")({
  beforeLoad: async ({ context }) => {
    const user = await currentUser(context.queryClient);
    if (!user) throw redirect({ to: "/login", replace: true });
    if (user.role !== "admin") throw redirect(homeLocation(user.role));
    return { user };
  },
  component: AdminHome,
});

/**
 * Служебный кабинет администратора (спека #56, раздел 6): заместители врачей и разбор
 * эскалаций. Данных пациентов здесь нет.
 */
function AdminHome() {
  const { user, queryClient } = Route.useRouteContext();
  const navigate = useNavigate();
  const logout = $api.useMutation("post", "/api/v1/auth/logout", {
    onSuccess: async () => {
      await queryClient.cancelQueries();
      queryClient.clear();
      await navigate({ to: "/login", replace: true });
    },
  });
  const [created, dismiss] = useAdminEvents();
  const all = $api.useQuery("get", ESCALATIONS, { params: { query: { status: "all" } } });
  const fresh = created ? all.data?.find((e) => e.alert_id === created) : undefined;

  return (
    <>
      <AppHeader
        persona="doctor"
        product="Штиль"
        actions={
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-ink-muted sm:inline">{user.full_name}</span>
            <Button
              variant="secondary"
              disabled={logout.isPending}
              onClick={() => logout.mutate({})}
            >
              Выйти
            </Button>
          </div>
        }
      />
      {fresh && (
        <section
          role="alert"
          aria-label="Эскалация"
          className="flex items-center gap-4 bg-required-soft px-8 py-3 text-sm"
        >
          <span className="flex items-center gap-2 font-semibold text-required">
            <StatusIcon status="required" decorative />
            Обязательно
          </span>
          <span className="flex-1 text-ink">{bannerText(fresh)}</span>
          <button type="button" className="text-ink-muted hover:text-ink" onClick={dismiss}>
            Скрыть
          </button>
        </section>
      )}
      <main className="mx-auto flex max-w-6xl flex-col gap-8 px-4 py-6">
        <h1 className="text-xl font-semibold">Служебный кабинет</h1>
        <EscalationsSection />
        <DeputiesSection />
      </main>
    </>
  );
}
