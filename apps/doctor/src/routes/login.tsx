import { BrandMark, LoginForm, Panel } from "@compass/ui";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { currentUser, homeLocation, isSessionUnavailable, ME_KEY } from "../session";

export const Route = createFileRoute("/login")({
  beforeLoad: async ({ context }) => {
    let user: Awaited<ReturnType<typeof currentUser>>;
    try {
      user = await currentUser(context.queryClient);
    } catch (error) {
      // Нет связи — форма остаётся, после восстановления «Войти» работает (кадр 01).
      if (isSessionUnavailable(error)) return;
      throw error;
    }
    if (user) throw redirect(homeLocation(user.role));
  },
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const { queryClient } = Route.useRouteContext();
  return (
    <main className="doctor-login">
      <Panel className="doctor-login__card">
        <header className="doctor-login__brand">
          <BrandMark persona="doctor" decorative />
          <h1>Штиль</h1>
          <p>Кабинет врача</p>
        </header>
        <LoginForm
          persona="doctor"
          onSuccess={(user) => {
            queryClient.setQueryData(ME_KEY, user);
            void navigate(homeLocation(user.role));
          }}
        />
      </Panel>
    </main>
  );
}
