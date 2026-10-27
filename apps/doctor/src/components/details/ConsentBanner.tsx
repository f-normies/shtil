import { Info } from "lucide-react";
import { CONSENT_REVOKED_EFFECT, consentRevokedLabel } from "../../card/consent";

/**
 * Плашка «Согласие отозвано» на всю ширину панели: подробности «Пациентов» и все разделы
 * карточки (спека #112, раздел 3.7; кадры «Врач v4 · 02в» и «05г»). Значок и слова,
 * не только цвет. Согласие действует — плашки нет.
 */
export function ConsentBanner({ revokedAt }: { revokedAt: string | null | undefined }) {
  const label = consentRevokedLabel(revokedAt);
  if (!label) return null;
  return (
    <div
      role="status"
      className="flex min-h-11 shrink-0 items-center gap-2 bg-surface-sunken px-8 py-1 text-sm leading-5"
    >
      <Info aria-hidden className="size-5 shrink-0 text-ink" />
      <p className="shrink-0 font-semibold text-ink">{label}</p>
      <p className="min-w-0 flex-1 text-ink-muted">{CONSENT_REVOKED_EFFECT}</p>
    </div>
  );
}
