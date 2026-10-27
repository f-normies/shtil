import type { HTMLAttributes } from "react";

export type AvatarSize = 24 | 36 | 48 | 64;

export type AvatarProps = Omit<HTMLAttributes<HTMLSpanElement>, "children"> & {
  initials: string;
  size?: AvatarSize;
  label?: string;
  decorative?: boolean;
};

/** Аватар показывает только инициалы; фотографии не загружаются. */
export function Avatar({
  initials,
  size = 36,
  label,
  decorative = !label,
  className = "",
  ...props
}: AvatarProps) {
  return (
    <span
      {...props}
      className={`ui-avatar ${className}`.trim()}
      data-size={size}
      role="img"
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : label}
    >
      {initials}
    </span>
  );
}
