/** Российский номер `+7XXXXXXXXXX` показываем как `+7 900 000-00-00`, прочие — как есть. */
export function formatPhone(phone: string): string {
  const ru = /^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/.exec(phone);
  return ru ? `+7 ${ru[1]} ${ru[2]}-${ru[3]}-${ru[4]}` : phone;
}
