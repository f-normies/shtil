import { $api } from "@compass/api-client";
import { Alert, Button } from "@compass/ui";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useRef, useState } from "react";
import { GuidancePanel } from "../../../../card/GuidancePanel";
import { refreshProposals, usePatient, usePatientProposals } from "../../../../card/queries";
import { openOffers, staleNotice } from "../../../../card/regimen";
import { revealOffer } from "../../../../card/therapy";
import { useInspector } from "../../../../components/inspector/context";
import { AskQuestion } from "../../../../components/knowledge/AskQuestion";
import { Section } from "../../../../components/signals/Section";
import { Diagnoses } from "../../../../components/therapy/Diagnoses";
import { Medications } from "../../../../components/therapy/Medications";
import { OfferGroup } from "../../../../components/therapy/OfferGroup";
import { Prescriptions } from "../../../../components/therapy/Prescriptions";
import { ProfileForm } from "../../../../components/therapy/ProfileForm";

/**
 * «Терапия» по кадру «Врач v4 · 10» (спека #28, раздел 8): сведения и диагнозы с подбором,
 * предложение назначения в пунктире, проверки по инструкции и решение врача, назначения,
 * варианты по КР (#107). Открытое предложение показано из списка предложений: повторный
 * подбор после перезагрузки не нужен.
 */
export const Route = createFileRoute("/_doctor/patients/$patientId/therapy")({
  component: Therapy,
});

function Therapy() {
  const { patientId } = Route.useParams();
  const patient = usePatient(patientId);
  const proposals = usePatientProposals(patientId);
  const queryClient = useQueryClient();
  const { close } = useInspector();
  const options = $api.useMutation("post", "/api/v1/patients/{patient_id}/therapy/options", {
    onSuccess: () => refreshProposals(queryClient),
  });
  const [notice, setNotice] = useState<string | null>(null);
  const changed = () => {
    options.reset();
    // Основание относится к прежним клиническим данным, пока врач не откроет его снова.
    close();
  };
  // Подбор занимает секунды, а результат появляется ниже экрана: по готовности первый блок
  // ответа прокручивается в поле зрения и получает фокус (M-5). Ссылка-функция зависит от
  // ответа, поэтому React вызывает её заново и для уже смонтированного заголовка.
  const reveal = useRef(false);
  const pick = () => {
    setNotice(null);
    reveal.current = true;
    options.mutate({ params: { path: { patient_id: patientId } } });
  };
  const answer = options.data;
  const revealFirst = useCallback(
    (el: HTMLHeadingElement | null) => {
      if (el && answer && reveal.current) {
        reveal.current = false;
        revealOffer(el);
      }
    },
    [answer],
  );
  // Решение сохранено: ответ подбора устарел, показываем свежий список предложений.
  const decided = (text: string) => {
    options.reset();
    setNotice(text);
  };
  // Итог решения получает фокус: группа предложения с кнопками уже исчезла.
  const focusNotice = useCallback((el: HTMLParagraphElement | null) => el?.focus(), []);
  // Ответ подбора полнее: в нём и препараты, которые назначить нельзя. Без подбора —
  // открытые предложения из списка.
  const offers = options.data?.options ?? openOffers(proposals.data ?? []);
  const stale = options.data ? null : staleNotice(proposals.data ?? []);

  return (
    <>
      <Section title="Сведения и диагнозы">
        <div className="flex flex-col gap-4">
          {patient.data && (
            <ProfileForm key={patient.data.id} patient={patient.data} onChanged={changed} />
          )}
          <Diagnoses patientId={patientId} onChanged={changed} />
          <div className="flex flex-col gap-2 border-t border-separator pt-4">
            <p className="text-[13px] text-ink-muted">
              Система проверяет препараты справочника по сведениям и диагнозам и предлагает схему по
              инструкции. Назначение действует только после вашего подтверждения.
            </p>
            <div>
              <Button loading={options.isPending} onClick={pick}>
                {options.isPending ? "Подбираем…" : "Подобрать терапию"}
              </Button>
            </div>
            {options.isError && (
              <Alert tone="error">не удалось выполнить подбор. Попробуйте ещё раз.</Alert>
            )}
            {options.data?.message && (
              <p className="text-[15px] text-ink">{options.data.message}.</p>
            )}
          </div>
        </div>
      </Section>

      {notice && (
        <p
          ref={focusNotice}
          role="status"
          tabIndex={-1}
          className="rounded-xl bg-surface px-4 py-3 text-[15px] font-semibold text-ink outline-none"
        >
          {notice}
        </p>
      )}
      {proposals.isPending && !options.data && (
        <div
          aria-busy
          className="flex animate-pulse flex-col gap-3 rounded-xl bg-surface p-4 motion-reduce:animate-none"
        >
          <span className="h-4 w-48 rounded bg-surface-sunken" />
          <span className="h-6 w-32 rounded bg-surface-sunken" />
          <span className="h-24 rounded bg-surface-sunken" />
        </div>
      )}
      {stale && (
        <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-[15px] text-ink">
          {stale}
          <Button variant="link" disabled={options.isPending} onClick={pick}>
            Подобрать заново
          </Button>
        </p>
      )}
      {proposals.isError && !options.data && (
        <p className="flex items-center justify-between rounded-xl bg-surface px-4 py-3 text-sm text-ink">
          Не удалось загрузить предложения
          <Button variant="link" onClick={() => void proposals.refetch()}>
            Повторить
          </Button>
        </p>
      )}
      {offers.map((o, i) => (
        <OfferGroup
          key={`${o.drug_id}/${o.indication_id}/${o.proposal?.id ?? "none"}`}
          offer={o}
          onDecided={decided}
          headingRef={i === 0 ? revealFirst : undefined}
        />
      ))}

      <Section title="Назначения">
        <Prescriptions patientId={patientId} onChanged={changed} />
      </Section>
      <Section title="Препараты вне системы">
        <Medications patientId={patientId} onChanged={changed} />
      </Section>
      <GuidancePanel patientId={patientId} />
      <AskQuestion />
    </>
  );
}
