import { $api } from "@compass/api-client";
import {
  clearMessagingOwnerAfterConfirmation,
  confirmMessagingOwner,
  getMessagingLocalCounts,
  lockMessagingAccess,
} from "@compass/ui";
import {
  createFileRoute,
  Outlet,
  redirect,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { RealtimeContext, useRealtime } from "../card/useRealtime";
import { Inspector } from "../components/inspector/Inspector";
import { SignalsBanner } from "../components/SignalsBanner";
import { DoctorMenu } from "../components/window/DoctorMenu";
import { LogoutDialog } from "../components/window/LogoutDialog";
import { currentUser, homeLocation } from "../session";

/**
 * Окно вошедшего врача (спека #13, раздел 10.1): меню 232 px слева, справа экран —
 * список и подробности или карточка. Окно на всю высоту, панели прокручиваются сами:
 * плашка сверху не выталкивает низ списка за экран. Здесь проверка входа и одно
 * соединение с WebSocket на весь кабинет (раздел 6).
 */
export const Route = createFileRoute("/_doctor")({
  beforeLoad: async ({ context }) => {
    const user = await currentUser(context.queryClient);
    if (!user) {
      throw redirect({ to: "/login", replace: true });
    }
    if (user.role !== "doctor") throw redirect(homeLocation(user.role));
    return { user };
  },
  component: DoctorLayout,
});

function DoctorLayout() {
  const { user, queryClient } = Route.useRouteContext();
  const navigate = useNavigate();
  const realtime = useRealtime();
  const pathname = useLocation({ select: (location) => location.pathname });
  const [logoutOpen, setLogoutOpen] = useState(false);
  const [localCounts, setLocalCounts] = useState({ drafts: 0, unsent: 0 });
  const [clearLocalOnExit, setClearLocalOnExit] = useState(false);
  const [countsLoading, setCountsLoading] = useState(false);
  const clearOnExitRef = useRef(false);

  useEffect(() => {
    confirmMessagingOwner(user.id, "doctor");
  }, [user.id]);

  const logout = $api.useMutation("post", "/api/v1/auth/logout", {
    onSuccess: async () => {
      try {
        if (clearOnExitRef.current) {
          await clearMessagingOwnerAfterConfirmation(user.id);
        } else {
          lockMessagingAccess();
        }
      } catch {
        lockMessagingAccess();
      } finally {
        queryClient.clear();
        navigate({ to: "/login", replace: true });
      }
    },
  });

  const openLogout = async () => {
    setLogoutOpen(true);
    setCountsLoading(true);
    setClearLocalOnExit(false);
    clearOnExitRef.current = false;
    try {
      setLocalCounts(await getMessagingLocalCounts());
    } catch {
      setLocalCounts({ drafts: 0, unsent: 0 });
    } finally {
      setCountsLoading(false);
    }
  };

  return (
    <RealtimeContext.Provider value={realtime}>
      <Inspector scope={pathname}>
        <div className="flex h-dvh overflow-hidden bg-canvas">
          <DoctorMenu doctorName={user.full_name} onLogout={() => void openLogout()} />
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <SignalsBanner />
            <Outlet />
          </div>
        </div>
      </Inspector>
      {logoutOpen && (
        <LogoutDialog
          counts={localCounts}
          countsLoading={countsLoading}
          clearLocal={clearLocalOnExit}
          pending={logout.isPending}
          failed={logout.isError}
          onClearLocalChange={(value) => {
            setClearLocalOnExit(value);
            clearOnExitRef.current = value;
          }}
          onConfirm={() => logout.mutate({})}
          onClose={() => setLogoutOpen(false)}
        />
      )}
    </RealtimeContext.Provider>
  );
}
