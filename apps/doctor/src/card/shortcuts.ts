/** ⌘F на Маке и Ctrl+F на остальных; в русской раскладке клавиша та же (`code`). */
export function isFindShortcut(event: {
  key: string;
  code?: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}): boolean {
  const modifier = event.metaKey !== event.ctrlKey;
  const letter = event.key.toLowerCase() === "f" || event.code === "KeyF";
  return modifier && letter && !event.altKey && !event.shiftKey;
}
