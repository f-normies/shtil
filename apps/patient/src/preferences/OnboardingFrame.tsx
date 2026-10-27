import { Alert, AppHeader, Button } from "@compass/ui";
import backIcon from "@compass/ui/assets/ikonka-vlevo.svg";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { ConsentIcon } from "../consent/ConsentIcon";
import sunriseRings from "./assets/sunrise-rings.svg";
import sunriseSun from "./assets/sunrise-sun.svg";
import { type OnboardingStepId, segmentClassName } from "./onboarding-flow";

type OnboardingFrameProps = {
  children: ReactNode;
  onBack?: () => void;
  completed?: boolean;
  progress?: string | null;
  /** Экран со сценой «Небо»: рассвет на приветствии, закат на «Всё готово». */
  scene?: "sunrise" | "sunset";
  stepNumber?: number;
  stepCount?: number;
  stepIds?: OnboardingStepId[];
  onSkip?: () => void;
  skipDisabled?: boolean;
};

export function OnboardingFrame({
  children,
  onBack,
  completed = false,
  progress,
  scene,
  stepNumber = 1,
  stepCount = 1,
  stepIds = [],
  onSkip,
  skipDisabled = false,
}: OnboardingFrameProps) {
  if (scene) {
    return (
      <main className="relative isolate flex min-h-dvh flex-col overflow-hidden bg-canvas">
        <SkyScene evening={scene === "sunset"} />
        <div className="relative z-10 flex min-h-dvh flex-col">
          <div className="flex h-[52px] shrink-0 items-center gap-2 pl-2 pr-3">
            {onBack ? (
              <button
                type="button"
                onClick={onBack}
                aria-label="Назад"
                className="grid size-11 shrink-0 place-items-center rounded-full text-ink focus-visible:outline-2 focus-visible:outline-primary"
              >
                <ConsentIcon src={backIcon} />
              </button>
            ) : (
              <span aria-hidden="true" className="w-11 shrink-0" />
            )}
            <ProgressSegments stepNumber={stepNumber} stepCount={stepCount} stepIds={stepIds} />
            {completed ? (
              <Link
                className="flex h-11 w-24 shrink-0 items-center justify-end text-right text-[15px] text-primary"
                to="/profile"
              >
                В профиль
              </Link>
            ) : onSkip ? (
              <button
                type="button"
                onClick={onSkip}
                disabled={skipDisabled}
                className="h-11 w-24 shrink-0 text-right text-[15px] text-primary disabled:opacity-60"
              >
                Пропустить
              </button>
            ) : (
              <span aria-hidden="true" className="w-24 shrink-0" />
            )}
          </div>
          <div
            className={`mx-auto flex w-full max-w-[360px] flex-1 flex-col px-5 pb-6 ${scene === "sunset" ? "pt-[236px]" : "pt-[358px]"}`}
          >
            {children}
          </div>
        </div>
      </main>
    );
  }

  return (
    <>
      <AppHeader
        persona="patient"
        product="Штиль"
        actions={
          completed ? (
            <Link className="text-sm underline" to="/profile">
              В профиль
            </Link>
          ) : undefined
        }
      />
      <main className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-lg flex-col gap-5 px-4 py-6 sm:py-8">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="-ml-2 min-h-10 w-fit px-2 text-sm font-medium text-primary focus-visible:outline-2 focus-visible:outline-primary"
          >
            ← Назад
          </button>
        ) : null}
        {progress && (
          <p
            className="text-xs font-medium uppercase tracking-wide text-ink-muted"
            aria-live="polite"
          >
            Шаг {progress}
          </p>
        )}
        <div className="flex-1 rounded-3xl border border-line bg-canvas p-5 shadow-sm sm:p-7">
          <div className="flex flex-col gap-5">{children}</div>
        </div>
        {!completed && (
          <p className="text-center text-xs text-ink-muted">
            Настройки можно изменить позже в профиле.
          </p>
        )}
      </main>
    </>
  );
}

function ProgressSegments({
  stepNumber,
  stepCount,
  stepIds,
}: {
  stepNumber: number;
  stepCount: number;
  stepIds: OnboardingStepId[];
}) {
  return (
    <div
      role="progressbar"
      aria-label={`Шаг ${stepNumber} из ${stepCount}`}
      aria-valuemin={1}
      aria-valuemax={stepCount}
      aria-valuenow={stepNumber}
      className="flex min-w-0 flex-1 gap-1"
    >
      {stepIds.map((stepId, index) => (
        <span key={stepId} aria-hidden="true" className={segmentClassName(index, stepNumber)} />
      ))}
    </div>
  );
}

/**
 * Сцена «Небо» (компонент «Пациент/Небо» в Figma): утром солнце над горизонтом,
 * вечером — опускается за него, небо персиковое. На «Всё готово» сцена поднята на 90 px,
 * как в кадре 11.
 */
function SkyScene({ evening }: { evening: boolean }) {
  return (
    <div
      role="img"
      aria-label={evening ? "Закат над морем" : "Рассвет над морем"}
      className={`pointer-events-none absolute inset-x-0 mx-auto h-[400px] w-full max-w-[360px] overflow-hidden ${evening ? "top-[-90px]" : "top-0"}`}
    >
      <div
        className={`absolute inset-x-0 top-0 h-[300px] ${evening ? "bg-accent-warm-soft" : "bg-accent-calm-soft"}`}
      />
      <span
        aria-hidden="true"
        className="absolute inset-x-0 top-0 block h-[300px] w-full"
        style={{
          maskImage: `url(${JSON.stringify(sunriseRings)})`,
          WebkitMaskImage: `url(${JSON.stringify(sunriseRings)})`,
          maskSize: "100% 100%",
          WebkitMaskSize: "100% 100%",
          backgroundColor: evening ? "var(--palette-peach-200)" : "var(--palette-brand-200)",
        }}
      />
      <div className="absolute inset-x-0 top-0 h-[300px] overflow-hidden">
        <img
          src={sunriseSun}
          alt=""
          className={`absolute left-1/2 h-16 w-32 -translate-x-1/2 ${evening ? "top-[264px]" : "top-[236px]"}`}
        />
      </div>
      <div className="absolute left-6 right-6 top-[298px] h-[3px] rounded-full bg-primary" />
      <div className="absolute left-1/3 right-1/3 top-[316px] h-[3px] rounded-full bg-primary" />
      <div className="absolute left-[40%] right-[40%] top-[334px] h-[3px] rounded-full bg-primary" />
      <div className="absolute left-[10%] top-[340px] h-[3px] w-12 rounded-full bg-primary/30" />
      <div className="absolute right-[10%] top-[324px] h-[3px] w-12 rounded-full bg-primary/30" />
    </div>
  );
}

export function OnboardingLoading({ error, retry }: { error: Error | null; retry: () => void }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-4 px-4 py-8">
      <h1 className="text-xl font-semibold">Настройки профиля</h1>
      {error ? (
        <>
          <Alert tone="error">Не удалось загрузить настройки. Попробуйте ещё раз.</Alert>
          <Button variant="secondary" onClick={retry}>
            Повторить
          </Button>
          <Link className="text-sm underline" to="/login">
            Перейти ко входу
          </Link>
        </>
      ) : (
        <p role="status" className="text-sm text-ink-muted">
          Загружаем настройки…
        </p>
      )}
    </main>
  );
}

/** Строка сводки «Всё готово»: иконка, подпись и значение (текст или кружки цветов). */
export function SummaryRow({
  icon,
  label,
  value,
  last = false,
}: {
  icon?: string;
  label: string;
  value: ReactNode;
  last?: boolean;
}) {
  return (
    <div className={`flex items-center gap-3 py-3 ${last ? "" : "border-b border-line"}`}>
      {icon && (
        <span className="text-primary-text [&>span]:size-5">
          <ConsentIcon src={icon} />
        </span>
      )}
      <dt className="min-w-0 flex-1 text-[15px] leading-5 text-ink-muted">{label}</dt>
      <dd className="m-0 text-[15px] leading-5 text-ink">{value}</dd>
    </div>
  );
}
