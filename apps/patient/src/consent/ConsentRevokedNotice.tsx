import infoIcon from "@compass/ui/assets/ikonka-info.svg";
import { ConsentIcon } from "./ConsentIcon";
import { CONSENT_REVOKED_TEXT } from "./revoke";

/** Пояснение на экране входа после отзыва согласия (спека #112, раздел 3.4). */
export function ConsentRevokedNotice({ text = CONSENT_REVOKED_TEXT }: { text?: string }) {
  return (
    <div role="status" className="consent-notice">
      <ConsentIcon src={infoIcon} />
      <p>{text}</p>
    </div>
  );
}
