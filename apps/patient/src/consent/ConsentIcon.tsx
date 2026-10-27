/**
 * Иконка из `@compass/ui` в цвете текста: SVG набора нарисованы одним цветом,
 * поэтому он служит маской, а цвет даёт `currentColor`.
 */
export function ConsentIcon({ src }: { src: string }) {
  const mask = `url(${JSON.stringify(src)})`;
  return (
    <span aria-hidden className="consent-icon" style={{ maskImage: mask, WebkitMaskImage: mask }} />
  );
}
