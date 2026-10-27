import { homePathFor } from "@compass/api-client";
import { Alert, MessagingChat } from "@compass/ui";
import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { PatientPage } from "../diary/PatientPage";
import { usePatientMessagingRealtime } from "../messaging/use-patient-messaging-realtime";
import { resolvePatientSession } from "../session";

export const Route = createFileRoute("/chat")({
  beforeLoad: async ({ context }) => {
    const session = await resolvePatientSession(context.queryClient);
    if (session.status === "unauthenticated") throw redirect({ to: "/login", replace: true });
    if (session.status === "other-role") {
      throw redirect({
        href: homePathFor(session.user.role),
        reloadDocument: true,
        replace: true,
      });
    }
    return { session };
  },
  component: PatientChatPage,
});

function PatientChatPage() {
  const { session, queryClient } = Route.useRouteContext();
  const online = session.status === "online";
  const sendTyping = usePatientMessagingRealtime(online ? session.user.id : null, queryClient);
  const chatProps = online
    ? {
        ownerId: session.user.id,
        role: "patient" as const,
        patientId: session.user.id,
        sendTyping,
      }
    : null;

  return (
    <PatientPage className="patient-chat" session={session}>
      {online ? (
        <main className="patient-chat__main">
          <h1 className="sr-only">Чат с лечащим врачом</h1>
          {chatProps && <MessagingChat {...chatProps} />}
        </main>
      ) : (
        <main className="patient-chat__offline">
          <h1 className="type-patient-title">Чат с лечащим врачом</h1>
          <Alert tone="error">
            Для открытия чата нужно подключение к интернету. Черновики и сообщения в очереди
            останутся на устройстве.
          </Alert>
          <Link className="patient-chat__login" to="/login">
            Перейти ко входу
          </Link>
        </main>
      )}
    </PatientPage>
  );
}
