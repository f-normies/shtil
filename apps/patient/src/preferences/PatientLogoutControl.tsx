import { PatientLogoutControl as DiaryLogoutControl } from "../diary/PatientLogoutControl";

/** Строка «Выйти» в профиле; подтверждение удаления локальных ответов — в общем компоненте. */
export function PatientLogoutControl({ ownerId }: { ownerId: string }) {
  return (
    <DiaryLogoutControl
      session={{ status: "offline", ownerId, cause: "network" }}
      appearance="row"
    />
  );
}
