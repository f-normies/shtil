function words(fullName: string): string[] {
  return fullName.trim().split(/\s+/).filter(Boolean);
}

/** «Соколова Ирина Павловна» → «Ирина Павловна»: how the patient screens address the doctor. */
export function doctorDisplayName(fullName: string): string {
  const parts = words(fullName);
  if (parts.length === 0) return "Ваш врач";
  if (parts.length === 1) return parts[0] ?? "Ваш врач";
  return parts.slice(1, 3).join(" ");
}

/** Avatar initials: first name, then surname («ИС»). */
export function doctorInitials(fullName: string): string {
  const parts = words(fullName);
  const first = (parts[1] ?? parts[0] ?? "")[0] ?? "";
  const surname = parts.length > 1 ? (parts[0]?.[0] ?? "") : "";
  return `${first}${surname}`.toLocaleUpperCase("ru-RU");
}
