import { createFileRoute, redirect } from "@tanstack/react-router";

/** Прежний адрес истории одной шкалы ведёт в раздел «Шкалы» (спека #13, раздел 10.9). */
export const Route = createFileRoute("/_doctor/patients/$patientId/scales/$scaleId")({
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/patients/$patientId/scales",
      params: { patientId: params.patientId },
      replace: true,
    });
  },
});
