import { CircleAlert, CircleCheck } from "lucide-react";
import type { ReactNode } from "react";

type Tone = "error" | "success";

const TONES: Record<Tone, { box: string; Icon: typeof CircleAlert; label: string }> = {
  error: { box: "bg-danger-soft text-danger border-danger", Icon: CircleAlert, label: "Ошибка" },
  success: {
    box: "bg-success-soft text-success border-success",
    Icon: CircleCheck,
    label: "Готово",
  },
};

/** Сообщение: смысл передают иконка и текстовая метка, не только цвет. */
export function Alert({ tone, children }: { tone: Tone; children: ReactNode }) {
  const { box, Icon, label } = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm ${box}`}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <p>
        <span className="font-semibold">{label}:</span> {children}
      </p>
    </div>
  );
}
