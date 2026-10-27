import { $api, retryAfterText } from "@compass/api-client";
import { Alert, Button, Panel } from "@compass/ui";
import { useState } from "react";

const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long", timeStyle: "short" });

/** Ссылка-приглашение: пациент регистрируется по ней и сразу привязывается к врачу. */
export function InvitePatient() {
  const [copied, setCopied] = useState(false);
  const invite = $api.useMutation("post", "/api/v1/patients/invites", {
    onMutate: () => setCopied(false),
  });
  const wait = retryAfterText(invite.error);
  const link = invite.data
    ? `${window.location.origin}/patient/register?invite=${encodeURIComponent(invite.data.token)}`
    : null;

  const copy = async () => {
    if (link) {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    }
  };

  return (
    <Panel title="Пригласить пациента">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-ink-muted">
          Пациент открывает ссылку, регистрируется и даёт согласие на обработку данных. После этого
          его самоотчёты попадают в вашу карточку.
        </p>
        {invite.isError && (
          <Alert tone="error">
            {wait !== null
              ? `слишком много приглашений за час. Повторите ${wait}.`
              : "не удалось создать приглашение."}
          </Alert>
        )}
        {link && invite.data ? (
          <div className="flex flex-col gap-2">
            <code className="break-all rounded-md border border-line bg-canvas px-3 py-2 text-sm">
              {link}
            </code>
            <p className="text-sm text-ink-muted">
              Действует до {dateTime.format(new Date(invite.data.expires_at))}, одноразовая.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button onClick={copy}>{copied ? "Скопировано" : "Скопировать ссылку"}</Button>
              <Button variant="secondary" onClick={() => invite.mutate({})}>
                Новая ссылка
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button onClick={() => invite.mutate({})} disabled={invite.isPending}>
              Создать ссылку-приглашение
            </Button>
          </div>
        )}
      </div>
    </Panel>
  );
}
