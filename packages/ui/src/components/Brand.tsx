import type { HTMLAttributes, ImgHTMLAttributes } from "react";

export type BrandPersona = "doctor" | "patient";

const MARKS: Record<BrandPersona, string> = {
  doctor: new URL("../assets/znak-vrach.svg", import.meta.url).href,
  patient: new URL("../assets/znak-patsient.svg", import.meta.url).href,
};

export type BrandMarkProps = Omit<ImgHTMLAttributes<HTMLImageElement>, "src" | "alt"> & {
  persona: BrandPersona;
  label?: string;
  decorative?: boolean;
};

/** Фирменный знак с точными пропорциями и локальным SVG из снимка Figma. */
export function BrandMark({
  persona,
  label = "Знак Штиль",
  decorative = !label,
  className = "",
  ...props
}: BrandMarkProps) {
  return (
    <img
      {...props}
      className={`ui-brand-mark ${className}`.trim()}
      src={MARKS[persona]}
      alt={decorative ? "" : label}
      aria-hidden={decorative || undefined}
    />
  );
}

export type BrandProps = Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  persona: BrandPersona;
  product?: string;
};

/** Знак и видимое название продукта для шапок и форм. */
export function Brand({ persona, product = "Штиль", className = "", ...props }: BrandProps) {
  return (
    <span {...props} className={`ui-brand ${className}`.trim()}>
      <BrandMark persona={persona} decorative />
      <span className="ui-brand__name">{product}</span>
    </span>
  );
}
