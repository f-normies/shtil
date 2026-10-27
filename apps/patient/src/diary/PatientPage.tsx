import type { ReactNode } from "react";
import type { PatientSession } from "../session";
import { PatientBottomNavigation } from "./PatientBottomNavigation";

/** Patient-only token scope and safe-area-aware navigation frame. */
export function PatientPage({
  children,
  className = "",
  navigation = true,
  session,
}: {
  children: ReactNode;
  className?: string;
  navigation?: boolean;
  /** Needed for the unread chat badge in the navigation. */
  session?: PatientSession;
}) {
  return (
    <div
      className={`patient-app ${className}`.trim()}
      data-tsvet="patsient-prokhladnaya"
      data-forma="patsient"
    >
      {children}
      {navigation && <PatientBottomNavigation session={session} />}
    </div>
  );
}
