/**
 * «Что сделать»: `doctor_actions` сигнала по порядку, дословно, нумерованным списком
 * (кадр 03). Один компонент для группы сигнала в подробностях и для экрана «Сигналы».
 */
export function ActionSteps({ actions }: { actions: readonly string[] }) {
  return (
    <ol className="flex flex-col divide-y divide-line">
      {actions.map((a, i) => (
        <li key={a} className="flex gap-4 py-2">
          <span className="text-ink-muted">{i + 1}</span>
          <span>{a}</span>
        </li>
      ))}
    </ol>
  );
}
