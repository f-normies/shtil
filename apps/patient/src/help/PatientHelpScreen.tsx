import phoneIcon from "@compass/ui/assets/ikonka-telefon.svg";
import closeIcon from "@compass/ui/assets/ikonka-zakryt.svg";
import { useEffect, useRef } from "react";
import { ConsentIcon } from "../consent/ConsentIcon";
import { usePatientHelp } from "./api";
import "./help.css";

/** Фраза о враче (спека #57, раздел 5.1): `null` — фразы нет. */
export type DoctorNotice = "sent" | "queued" | null;

const NOTICE_TEXT: Record<Exclude<DoctorNotice, null>, string> = {
  sent: "Мы сообщили вашему врачу.",
  queued: "Сообщим врачу, как только появится связь.",
};

/**
 * Экран помощи по кадру «Пациент · 26» поверх всего. Ничего не отправляет и не меняет:
 * только номера и ссылки `tel:`. Красный цвет в кабинете пациента — только здесь.
 */
export function PatientHelpScreen({
  notice,
  backLabel,
  onClose,
}: {
  notice: DoctorNotice;
  backLabel: string;
  onClose: () => void;
}) {
  const { content } = usePatientHelp();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const callRef = useRef<HTMLAnchorElement>(null);
  const primary = content.contacts.find((contact) => contact.primary);
  const others = content.contacts.filter((contact) => !contact.primary);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    // Фокус — на звонке: это главное действие экрана.
    callRef.current?.focus();
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="patient-help-title"
      className="patient-help"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="patient-help__frame">
        <button
          type="button"
          className="patient-help__close"
          aria-label="Закрыть"
          onClick={onClose}
        >
          <ConsentIcon src={closeIcon} />
        </button>
        <div className="patient-help__content">
          <span className="patient-help__badge" aria-hidden>
            <ConsentIcon src={phoneIcon} />
          </span>
          <h1 id="patient-help-title" className="type-patient-greeting">
            {content.title}
          </h1>
          {(content.text || notice) && (
            <p className="type-patient-body">
              {[content.text, notice && NOTICE_TEXT[notice]].filter(Boolean).join(" ")}
            </p>
          )}
          <div className="patient-help__calls">
            {primary && (
              // Ссылка, а не кнопка: звонок открывает система телефона.
              <a
                ref={callRef}
                className="ui-button w-full"
                data-variant="crisis"
                href={`tel:${primary.number}`}
              >
                <ConsentIcon src={phoneIcon} />
                {primary.label}
              </a>
            )}
            {others.map((contact) => (
              <a
                key={contact.id}
                className="ui-button w-full"
                data-variant="secondary"
                href={`tel:${contact.number}`}
              >
                <ConsentIcon src={phoneIcon} />
                {contact.label}
              </a>
            ))}
          </div>
          <HelpSources content={content} />
        </div>
        <button
          type="button"
          className="ui-button patient-help__back"
          data-variant="quiet"
          onClick={onClose}
        >
          {backLabel}
        </button>
      </div>
    </dialog>
  );
}

function HelpSources({ content }: { content: ReturnType<typeof usePatientHelp>["content"] }) {
  const refs = [...content.sources, ...content.contacts.flatMap((contact) => contact.sources)];
  const unique = refs.filter(
    (ref, index) =>
      refs.findIndex((other) => other.source === ref.source && other.locator === ref.locator) ===
      index,
  );
  if (unique.length === 0) return null;
  return (
    <details className="patient-help__sources type-patient-body-small">
      <summary>Источники</summary>
      <ul>
        {unique.map((ref) => (
          <li key={`${ref.source}:${ref.locator}`}>
            <a href={ref.url} target="_blank" rel="noreferrer">
              {ref.title}
            </a>
            , {ref.locator}
          </li>
        ))}
      </ul>
      {content.checked_on && <p>Проверено {formatDate(content.checked_on)}</p>}
    </details>
  );
}

function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-");
  return `${day}.${month}.${year}`;
}
