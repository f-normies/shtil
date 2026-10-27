import type { OwnerPointer } from "../assessments/types";

export function canReadCachedPreferences(
  pointer: OwnerPointer | null,
  ownerId: string,
  accessDenied: boolean,
): boolean {
  return !accessDenied && pointer?.role === "patient" && pointer.id === ownerId && !pointer.locked;
}
