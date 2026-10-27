import diaryIcon from "@compass/ui/assets/ikonka-dnevnik.svg";
import homeIcon from "@compass/ui/assets/ikonka-glavnaya.svg";
import profileIcon from "@compass/ui/assets/ikonka-profil.svg";
import chatIcon from "@compass/ui/assets/ikonka-soobshcheniya.svg";
import { Link, useRouterState } from "@tanstack/react-router";
import { useUnreadMessageCount } from "../messaging/doctor-queries";
import type { PatientSession } from "../session";

const destinations = [
  { label: "Сегодня", to: "/", image: homeIcon },
  { label: "Дневник", to: "/diary", image: diaryIcon },
  { label: "Чат", to: "/chat", image: chatIcon },
  { label: "Профиль", to: "/profile", image: profileIcon },
] as const;

/** The fixed four-item patient navigation shared by every diary screen. */
export function PatientBottomNavigation({ session }: { session?: PatientSession }) {
  const unreadCount = useUnreadMessageCount(session ?? { status: "unauthenticated" });
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const active =
    pathname === "/" || pathname === "/check-in"
      ? "/"
      : pathname.startsWith("/profile")
        ? "/profile"
        : pathname.startsWith("/chat")
          ? "/chat"
          : "/diary";
  return (
    <nav className="patient-bottom-nav" aria-label="Основная навигация">
      {destinations.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          className="patient-bottom-nav__item"
          data-active={active === item.to || undefined}
          aria-current={active === item.to ? "page" : undefined}
        >
          <span className="patient-bottom-nav__icon">
            <img src={item.image} alt="" aria-hidden="true" />
            {item.to === "/chat" && unreadCount > 0 && (
              <span className="patient-bottom-nav__badge" aria-hidden="true">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}
          </span>
          <span>
            {item.label}
            {item.to === "/chat" && unreadCount > 0 && (
              <span className="sr-only">, непрочитанных: {unreadCount}</span>
            )}
          </span>
        </Link>
      ))}
    </nav>
  );
}
