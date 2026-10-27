import type { Schemas } from "@compass/api-client";
import { Button } from "@compass/ui";
import diaryIcon from "@compass/ui/assets/ikonka-dnevnik.svg";
import settingsIcon from "@compass/ui/assets/ikonka-nastroyki.svg";
import profileIcon from "@compass/ui/assets/ikonka-profil.svg";
import messagesIcon from "@compass/ui/assets/ikonka-soobshcheniya.svg";
import { ConsentIcon } from "./ConsentIcon";
import { CONSENT_SUMMARY, formatConsentVersion } from "./registration";

const ICONS: Record<(typeof CONSENT_SUMMARY)[number]["id"], string> = {
  data: diaryIcon,
  doctor: profileIcon,
  notice: messagesIcon,
  revoke: settingsIcon,
};

/**
 * Экран согласия на обработку данных при регистрации (кадр «Пациент · 03»): четыре пункта
 * кратко, версия и полный текст с сервера. «Принимаю» ставит галочку в форме.
 */
export function ConsentDocumentScreen({
  document,
  onAccept,
  onBack,
}: {
  document: Schemas["ConsentDocument"];
  onAccept: () => void;
  onBack: () => void;
}) {
  return (
    <main className="consent-revoke">
      <h1 className="consent-revoke__title">Согласие на обработку данных</h1>
      <p className="m-0 w-fit rounded-full bg-surface-sunken px-2.5 py-1 text-[13px] font-medium leading-[18px] text-ink-muted">
        {formatConsentVersion(document.version)}
      </p>
      <ul className="consent-revoke__card">
        {CONSENT_SUMMARY.map((point) => (
          <li key={point.id} className="consent-revoke__item">
            <span className="consent-revoke__badge">
              <ConsentIcon src={ICONS[point.id]} />
            </span>
            <div>
              <p className="consent-revoke__label">{point.title}</p>
              <p className="consent-revoke__text">{point.text}</p>
            </div>
          </li>
        ))}
      </ul>
      <details className="rounded-2xl bg-surface px-4 py-3">
        <summary className="cursor-pointer text-[15px] font-medium text-primary-text">
          Прочитать полностью
        </summary>
        <p className="mt-2 whitespace-pre-line text-[15px] leading-5 text-ink">{document.text}</p>
      </details>
      <div className="consent-revoke__actions">
        <Button onClick={onAccept}>Принимаю</Button>
        <Button variant="quiet" onClick={onBack}>
          Вернуться к форме
        </Button>
      </div>
    </main>
  );
}
