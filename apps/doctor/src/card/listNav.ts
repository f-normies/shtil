/**
 * Клавиатура списков с одной остановкой Tab (кадры 02 и 03): ↑↓ меняют строку, Home и End —
 * первая и последняя. `at` = −1, когда активной строки нет. `null` — клавиша не про список.
 */
export function nextIndex(key: string, at: number, length: number): number | null {
  if (length === 0) return null;
  switch (key) {
    case "ArrowDown":
      return Math.min(at + 1, length - 1);
    case "ArrowUp":
      return Math.max(at - 1, 0);
    case "Home":
      return 0;
    case "End":
      return length - 1;
    default:
      return null;
  }
}
