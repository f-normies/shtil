import { useEffect, useMemo, useState } from "react";
import * as UI from "../../src";

const buttonImage = new URL("../../src/assets/ikonka-glaz.svg", import.meta.url).href;
const DYNAMIC_ICON_EVENT = "visual-review:dynamic-icon";
type DynamicIconShape = "circle" | "square" | "inline";
const dynamicIconImages = {
  circle: `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="#151D23"/></svg>')}#circle`,
  square: `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" fill="#151D23"/></svg>')}#square`,
};

const referenceImages = {
  "required-selected": new URL(
    "../../../../docs/design/figma/components/doctor-atomy/assets/img14.svg",
    import.meta.url,
  ).href,
  proposed: new URL(
    "../../../../docs/design/figma/components/doctor-atomy/assets/img11.svg",
    import.meta.url,
  ).href,
  "doctor-eye-normal": new URL(
    "../../../../docs/design/figma/doctor/01-vkhod/assets/img1.svg",
    import.meta.url,
  ).href,
  "doctor-eye-visible": new URL(
    "../../../../docs/design/figma/doctor/01b-vkhod-parol-pokazan/assets/img1.svg",
    import.meta.url,
  ).href,
  "patient-eye-normal": new URL(
    "../../../../docs/design/figma/patient/01-vkhod/assets/img2.svg",
    import.meta.url,
  ).href,
};

function ButtonSvgIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      <path d="M4 4h16v16H4z" fill="#151D23" />
    </svg>
  );
}

function ImageButtonIcon() {
  return <img src={buttonImage} alt="" />;
}

function StatefulImageButtonIcon() {
  const [shape, setShape] = useState<DynamicIconShape>("circle");

  useEffect(() => {
    const updateShape = (event: Event) => {
      const nextShape = (event as CustomEvent<DynamicIconShape>).detail;
      setShape(nextShape);
    };
    window.addEventListener(DYNAMIC_ICON_EVENT, updateShape);
    return () => window.removeEventListener(DYNAMIC_ICON_EVENT, updateShape);
  }, []);

  if (shape === "inline") {
    return (
      <svg aria-hidden="true" data-testid="dynamic-icon-inline-svg" viewBox="0 0 24 24">
        <path d="M3 3h18v18H3z" fill="#151D23" />
      </svg>
    );
  }

  return <img data-testid="dynamic-icon-image" src={dynamicIconImages[shape]} alt="" />;
}

export function DynamicImageIconCase() {
  const [showButton, setShowButton] = useState(true);
  const icon = useMemo(() => <StatefulImageButtonIcon />, []);
  const changeShape = (shape: DynamicIconShape) => {
    window.dispatchEvent(new CustomEvent<DynamicIconShape>(DYNAMIC_ICON_EVENT, { detail: shape }));
  };

  return (
    <main className="ui-fixture">
      <button data-testid="dynamic-icon-square" onClick={() => changeShape("square")} type="button">
        Square
      </button>
      <button
        data-testid="dynamic-icon-select-inline"
        onClick={() => changeShape("inline")}
        type="button"
      >
        Inline
      </button>
      <button data-testid="dynamic-icon-circle" onClick={() => changeShape("circle")} type="button">
        Circle
      </button>
      <button data-testid="dynamic-icon-unmount" onClick={() => setShowButton(false)} type="button">
        Unmount
      </button>
      {showButton && (
        <UI.Button data-testid="dynamic-icon-button" icon={icon} variant="primary">
          Dynamic icon
        </UI.Button>
      )}
    </main>
  );
}

export function VisualReviewCase() {
  return (
    <main className="ui-fixture">
      <section aria-label="Visual review statuses" className="ui-fixture__section">
        <UI.StatusBadge data-testid="selected-required" status="required" selected />
        <UI.StatusBadge data-testid="proposed-status" status="proposed" />
      </section>
      <section aria-label="Visual review buttons" className="ui-fixture__section">
        <UI.Button data-testid="primary-image" icon={<ImageButtonIcon />} variant="primary">
          Primary image
        </UI.Button>
        <UI.Button data-testid="primary-svg" icon={<ButtonSvgIcon />} variant="primary">
          Primary SVG
        </UI.Button>
        <UI.Button data-testid="quiet-image" icon={<ImageButtonIcon />} variant="quiet">
          Quiet image
        </UI.Button>
        <UI.Button data-testid="quiet-svg" icon={<ButtonSvgIcon />} variant="quiet">
          Quiet SVG
        </UI.Button>
        <UI.Button
          data-testid="link-image-count"
          count={4}
          icon={<ImageButtonIcon />}
          variant="link"
        >
          Ссылка
        </UI.Button>
        <UI.Button data-testid="quiet-active" icon={<ButtonSvgIcon />} variant="quiet">
          Тихая
        </UI.Button>
        <UI.Button data-testid="link-active" icon={<ButtonSvgIcon />} variant="link">
          Ссылка
        </UI.Button>
        <UI.Button
          data-testid="primary-mixed"
          endIcon={<ButtonSvgIcon />}
          icon={<ImageButtonIcon />}
          variant="primary"
        >
          Start and end icons
        </UI.Button>
      </section>
      <section aria-label="Visual review fields" className="ui-fixture__section">
        <UI.TextField
          defaultValue="sokolova@clinic.example"
          hint="Подсказка под адресом"
          label="Электронная почта"
        />
        <UI.TextField defaultValue="secret123" label="Пароль" type="password" />
      </section>
      <section aria-label="Visual review counters" className="ui-fixture__section">
        <UI.Counter aria-label="Нейтральный счётчик" value={4} />
        <UI.Counter aria-label="Обязательный счётчик" tone="required" value={4} />
        <UI.Counter aria-label="Желательный счётчик" tone="advised" value={4} />
      </section>
      <section aria-label="Visual review popover" className="ui-fixture__section">
        <UI.Popover label="Пояснение" trigger={<span>Открыть поповер</span>}>
          <p>Текст с источником.</p>
        </UI.Popover>
      </section>
      <section aria-label="Visual review segments" className="ui-fixture__section">
        <UI.SegmentedControl
          ariaLabel="Фильтр списка"
          onValueChange={() => undefined}
          options={[
            { value: "all", label: "Все" },
            { value: "alerts", label: "С сигналами" },
            { value: "invited", label: "Приглашённые" },
          ]}
          value="all"
        />
      </section>
    </main>
  );
}

export function VisualSourceCase({ source }: { source: string }) {
  const image = referenceImages[source as keyof typeof referenceImages];
  if (!image) throw new Error(`Unknown visual reference: ${source}`);
  return <img alt="" data-testid="visual-source" src={image} />;
}
