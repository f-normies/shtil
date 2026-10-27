import { createFileRoute, useRouter } from "@tanstack/react-router";
import { PatientHelpScreen } from "../help/PatientHelpScreen";

// Экран помощи из профиля или с главной (спека #57, раздел 5.3). Вход не нужен: экран
// берёт сохранённые данные или запасной номер 112 и ничего не отправляет.
export const Route = createFileRoute("/help")({
  component: HelpPage,
});

function HelpPage() {
  const router = useRouter();
  const back = () => {
    if (router.history.canGoBack()) router.history.back();
    else void router.navigate({ to: "/", replace: true });
  };
  return <PatientHelpScreen notice={null} backLabel="Назад" onClose={back} />;
}
