import { $api, type Schemas } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { refreshTherapy } from "../../card/queries";

type Patient = Schemas["PatientSummary"];
type Tri = "" | "yes" | "no";

const tri = (value: boolean | null | undefined): Tri =>
  value === true ? "yes" : value === false ? "no" : "";
const fromTri = (value: Tri): boolean | null => (value === "" ? null : value === "yes");

/**
 * Профиль для проверок по инструкции: возраст (спека #28), беременность, грудное
 * вскармливание, СКФ и класс по Чайлд-Пью (спека #29). Пустое значение — «не уточнено»:
 * проверка попросит врача уточнить, а не пропустит противопоказание.
 */
export function ProfileForm({ patient, onChanged }: { patient: Patient; onChanged: () => void }) {
  const queryClient = useQueryClient();
  const [birthDate, setBirthDate] = useState(patient.birth_date ?? "");
  const [sex, setSex] = useState(patient.sex ?? "");
  const [pregnant, setPregnant] = useState<Tri>(tri(patient.pregnant));
  const [breastfeeding, setBreastfeeding] = useState<Tri>(tri(patient.breastfeeding));
  const [egfr, setEgfr] = useState(patient.egfr?.toString() ?? "");
  const [egfrOn, setEgfrOn] = useState(patient.egfr_measured_on ?? "");
  const [childPugh, setChildPugh] = useState(patient.child_pugh ?? "");
  const save = $api.useMutation("put", "/api/v1/patients/{patient_id}/profile", {
    onSuccess: async () => {
      onChanged();
      await refreshTherapy(queryClient);
    },
  });
  const female = sex === "female";

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate({
          params: { path: { patient_id: patient.id } },
          body: {
            birth_date: birthDate || null,
            sex: sex === "female" || sex === "male" ? sex : null,
            pregnant: female ? fromTri(pregnant) : null,
            breastfeeding: female ? fromTri(breastfeeding) : null,
            egfr: egfr === "" ? null : Number(egfr),
            egfr_measured_on: egfrOn || null,
            child_pugh:
              childPugh === "none" || childPugh === "A" || childPugh === "B" || childPugh === "C"
                ? childPugh
                : null,
          },
        });
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Дата рождения
          <input
            className="min-h-11 rounded-md border border-line px-3"
            type="date"
            value={birthDate}
            onChange={(e) => setBirthDate(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Пол
          <select
            className="min-h-11 rounded-md border border-line px-3"
            value={sex}
            onChange={(e) => setSex(e.target.value)}
          >
            <option value="">не указан</option>
            <option value="female">женский</option>
            <option value="male">мужской</option>
          </select>
        </label>
        {female && (
          <>
            <TriField label="Беременность" value={pregnant} onChange={setPregnant} />
            <TriField
              label="Грудное вскармливание"
              value={breastfeeding}
              onChange={setBreastfeeding}
            />
          </>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          СКФ, мл/мин
          <input
            className="min-h-11 w-28 rounded-md border border-line px-3"
            type="number"
            min={1}
            max={200}
            value={egfr}
            onChange={(e) => setEgfr(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Дата анализа
          <input
            className="min-h-11 rounded-md border border-line px-3"
            type="date"
            value={egfrOn}
            onChange={(e) => setEgfrOn(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Печёночная недостаточность (Чайлд-Пью)
          <select
            className="min-h-11 rounded-md border border-line px-3"
            value={childPugh}
            onChange={(e) => setChildPugh(e.target.value)}
          >
            <option value="">не уточнено</option>
            <option value="none">нет</option>
            <option value="A">класс A</option>
            <option value="B">класс B</option>
            <option value="C">класс C</option>
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={save.isPending}>
          Сохранить
        </Button>
        {save.isSuccess && <span className="text-sm text-ink-muted">Сохранено</span>}
      </div>
      {save.isError && (
        <Alert tone="error">не удалось сохранить. Проверьте даты и СКФ (1–200).</Alert>
      )}
    </form>
  );
}

function TriField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Tri;
  onChange: (value: Tri) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      {label}
      <select
        className="min-h-11 rounded-md border border-line px-3"
        value={value}
        onChange={(e) => onChange(e.target.value as Tri)}
      >
        <option value="">не уточнено</option>
        <option value="yes">да</option>
        <option value="no">нет</option>
      </select>
    </label>
  );
}
