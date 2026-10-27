import {
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "link" | "crisis" | "soft";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  loading?: boolean;
  icon?: ReactNode;
  endIcon?: ReactNode;
  count?: number | string;
};

function ButtonIcon({
  icon,
  className,
}: {
  icon: ReactNode;
  className: "ui-button__icon" | "ui-button__end-icon";
}) {
  const containerRef = useRef<HTMLSpanElement>(null);
  const [normalized, setNormalized] = useState<{ icon: ReactNode; source: string | null } | null>(
    null,
  );

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateSource = () => {
      const images = container.querySelectorAll("img");
      const image = images.length === 1 ? images[0] : null;
      const responsiveSource =
        image?.srcset || image?.closest("picture")?.querySelector("source[srcset]");
      const source = responsiveSource ? image?.currentSrc || image?.src : image?.src;
      const isSvgSource =
        source &&
        (/\.svg(?:[?#]|$)/i.test(source) || /^data:image\/svg\+xml(?:;[^,]*)?,/i.test(source));
      const svgSource = isSvgSource ? source : null;
      setNormalized((current) =>
        current && current.icon === icon && current.source === svgSource
          ? current
          : { icon, source: svgSource },
      );
    };

    const handleImageLoad = (event: Event) => {
      if (event.target instanceof HTMLImageElement) updateSource();
    };
    const observer = new MutationObserver(updateSource);
    observer.observe(container, {
      attributes: true,
      attributeFilter: ["src", "srcset"],
      childList: true,
      subtree: true,
    });
    container.addEventListener("load", handleImageLoad, true);
    updateSource();
    return () => {
      observer.disconnect();
      container.removeEventListener("load", handleImageLoad, true);
    };
  }, [icon]);

  const source = normalized && normalized.icon === icon ? normalized.source : null;
  const style: CSSProperties | undefined = source
    ? {
        maskImage: `url(${JSON.stringify(source)})`,
        WebkitMaskImage: `url(${JSON.stringify(source)})`,
      }
    : undefined;

  return (
    <span ref={containerRef} className={className} aria-hidden="true">
      <span className={`ui-button__icon-source${source ? " ui-button__icon-source--masked" : ""}`}>
        {icon}
      </span>
      {source && <span className="ui-button__icon-image" style={style} />}
    </span>
  );
}

/** Кнопка использует плотность и форму текущего режима data-forma. */
export function Button({
  variant = "primary",
  className = "",
  loading = false,
  icon,
  endIcon,
  count,
  children,
  disabled = false,
  "aria-busy": ariaBusy,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading;

  return (
    <button
      {...props}
      className={`ui-button ${className}`.trim()}
      data-variant={variant}
      disabled={isDisabled}
      aria-busy={loading ? true : ariaBusy}
    >
      {loading ? (
        <span className="ui-button__spinner" aria-hidden="true" />
      ) : icon ? (
        <ButtonIcon icon={icon} className="ui-button__icon" />
      ) : null}
      <span className="ui-button__label">{children}</span>
      {count !== undefined && <span className="ui-button__count">{count}</span>}
      {endIcon && !loading && <ButtonIcon icon={endIcon} className="ui-button__end-icon" />}
    </button>
  );
}
