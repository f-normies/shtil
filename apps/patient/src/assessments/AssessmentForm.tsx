import type { Schemas } from "@compass/api-client";
import { Alert, AppHeader, Button, Panel } from "@compass/ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { usePatientHelp } from "../help/api";
import { type DoctorNotice, PatientHelpScreen } from "../help/PatientHelpScreen";
import { type SubmissionOutcome, submissionOutcome } from "../help/submission-outcome";
import { evaluateHelpTriggers } from "../help/triggers";
import { usePatientSyncState } from "../patient-sync-context";
import { getAssessmentStore, type PatientSession } from "../session";
import { DefinitionSources } from "./AssessmentCatalog";
import { AssessmentDraftVersionRecovery } from "./AssessmentDraftVersionRecovery";
import { fetchAssessmentDefinition } from "./api";
import { type AssessmentPersistedReceipt, persistAssessmentDraft } from "./persisted-callback";

type SaveState = "ready" | "saving" | "saved" | "error" | "conflict";

export function AssessmentForm({
  ownerId,
  scaleId,
  session,
  onPersisted,
  onFinished,
  onUnavailable,
  embedded = false,
}: {
  ownerId: string;
  scaleId: string;
  session: PatientSession;
  onPersisted?: (receipt: AssessmentPersistedReceipt) => void | Promise<void>;
  /** Исход известен и экран помощи закрыт: поток отметки может идти дальше (P2-01). */
  onFinished?: (outcome: SubmissionOutcome) => void;
  onUnavailable?: () => void;
  embedded?: boolean;
}) {
  const queryClient = useQueryClient();
  const { assessmentSyncNow: syncNow, sessionRevalidating } = usePatientSyncState();
  const online = session.status === "online";
  const definitionQuery = useQuery({
    queryKey: ["assessment-definition", ownerId, scaleId],
    queryFn: async () => {
      const store = await getAssessmentStore();
      if (!online || !navigator.onLine) {
        const cached = (await store.getCachedDefinitions())
          .filter((candidate) => candidate.scale.id === scaleId)
          .sort((left, right) => right.scale.version - left.scale.version)[0];
        if (!cached) throw new Error("Определение опросника не сохранено на этом устройстве.");
        return cached;
      }
      try {
        const definition = await fetchAssessmentDefinition(scaleId);
        await store.cacheDefinition(definition);
        return definition;
      } catch (error) {
        if (!(error instanceof TypeError)) throw error;
        const cached = (await store.getCachedDefinitions())
          .filter((candidate) => candidate.scale.id === scaleId)
          .sort((left, right) => right.scale.version - left.scale.version)[0];
        if (!cached) throw error;
        return cached;
      }
    },
    staleTime: 0,
    retry: false,
  });

  const definition = definitionQuery.data;
  useEffect(() => {
    if (definitionQuery.isError) onUnavailable?.();
  }, [definitionQuery.isError, onUnavailable]);
  const draftKey = ["assessment-draft", ownerId, scaleId] as const;
  const draftQuery = useQuery({
    queryKey: draftKey,
    queryFn: async () => {
      const store = await getAssessmentStore();
      return store.getDraft({ ownerId, scaleId });
    },
    enabled: Boolean(definition),
    retry: false,
  });
  const draftViewKey = `${ownerId}:${scaleId}:${definition?.scale.version ?? "loading"}`;
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [loadedDraftViewKey, setLoadedDraftViewKey] = useState("");
  const answersRef = useRef<Record<string, number>>({});
  const locallyEditedDraftViewKeyRef = useRef<string | null>(null);
  const revisionRef = useRef(0);
  const saveChainRef = useRef<Promise<void>>(Promise.resolve());
  const saveSequenceRef = useRef(0);
  const saveFailedRef = useRef(false);
  const conflictRef = useRef(false);
  const questionRefs = useRef(new Map<string, HTMLInputElement>());
  const [saveState, setSaveState] = useState<SaveState>("ready");
  const [storageError, setStorageError] = useState(false);
  const [formError, setFormError] = useState(false);
  const [submissionState, setSubmissionState] = useState<"idle" | "pending" | "saved">("idle");
  const [submitting, setSubmitting] = useState(false);
  // Экран помощи (спека #57): открыт — фраза о враче или null, закрыт — undefined.
  const [helpNotice, setHelpNotice] = useState<DoctorNotice | undefined>(undefined);
  const pendingFinishRef = useRef<SubmissionOutcome | null>(null);
  const { help } = usePatientHelp();
  const draftVersionMismatch = Boolean(
    definition &&
      draftQuery.data &&
      draftQuery.data.definition_snapshot.scale.version !== definition.scale.version,
  );
  const draftReady = loadedDraftViewKey === draftViewKey && !draftQuery.isPending;
  const conflict = saveState === "conflict";

  useEffect(() => {
    if (!definition || draftQuery.isPending) return;
    if (locallyEditedDraftViewKeyRef.current === draftViewKey) return;
    const draft = draftQuery.data;
    if (draft && draft.definition_snapshot.scale.version !== definition.scale.version) {
      answersRef.current = {};
      revisionRef.current = 0;
      setAnswers({});
      setLoadedDraftViewKey(draftViewKey);
      return;
    }
    answersRef.current = draft?.answers ?? {};
    revisionRef.current = draft?.revision ?? 0;
    setAnswers(answersRef.current);
    setLoadedDraftViewKey(draftViewKey);
  }, [definition, draftQuery.data, draftQuery.isPending, draftViewKey]);

  const saveAnswers = (nextAnswers: Record<string, number>) => {
    // After a cross-tab conflict only a reload may replace the newer stored answers.
    if (draftVersionMismatch || conflictRef.current) return;
    locallyEditedDraftViewKeyRef.current = draftViewKey;
    answersRef.current = nextAnswers;
    setAnswers(nextAnswers);
    setFormError(false);
    setStorageError(false);
    saveFailedRef.current = false;
    const sequence = ++saveSequenceRef.current;
    setSaveState("saving");
    const save = saveChainRef.current
      .catch(() => undefined)
      .then(async () => {
        if (conflictRef.current || !definition) return;
        const store = await getAssessmentStore();
        const result = await store.saveDraft({
          ownerId,
          scaleId,
          expectedRevision: revisionRef.current,
          answers: nextAnswers,
          definitionSnapshot: definition,
        });
        if (result.status === "conflict") {
          conflictRef.current = true;
          saveFailedRef.current = true;
          if (result.currentDraft) {
            revisionRef.current = result.currentDraft.revision;
            answersRef.current = result.currentDraft.answers;
            setAnswers(result.currentDraft.answers);
            queryClient.setQueryData(draftKey, result.currentDraft);
          }
          setSaveState("conflict");
          return;
        }
        revisionRef.current = result.draft.revision;
        queryClient.setQueryData(draftKey, result.draft);
        if (sequence === saveSequenceRef.current) setSaveState("saved");
      })
      .catch(() => {
        saveFailedRef.current = true;
        setStorageError(true);
        if (sequence === saveSequenceRef.current) setSaveState("error");
      });
    saveChainRef.current = save;
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!definition || draftVersionMismatch || submitting) return;
    setFormError(false);
    setSubmitting(true);
    await saveChainRef.current;
    if (saveFailedRef.current || conflictRef.current) {
      setSubmitting(false);
      return;
    }
    const missing = definition.scale.items.find(
      (item) => !Object.hasOwn(answersRef.current, item.id),
    );
    if (missing) {
      setFormError(true);
      setSubmitting(false);
      questionRefs.current.get(missing.id)?.focus();
      return;
    }

    const payload: Schemas["AssessmentCreateIn"] = {
      patient_id: ownerId,
      submission_id: crypto.randomUUID(),
      scale_id: scaleId,
      scale_version: definition.scale.version,
      completed_at: new Date().toISOString(),
      answers: { ...answersRef.current },
    };
    try {
      const store = await getAssessmentStore();
      const queued = await persistAssessmentDraft(
        store,
        {
          ownerId,
          scaleId,
          expectedRevision: revisionRef.current,
          payload,
        },
        onPersisted,
      );
      if (queued.status === "conflict") {
        conflictRef.current = true;
        setSaveState("conflict");
        setSubmitting(false);
        return;
      }
      revisionRef.current = 0;
      locallyEditedDraftViewKeyRef.current = null;
      answersRef.current = {};
      setAnswers({});
      queryClient.setQueryData(draftKey, null);
      await queryClient.invalidateQueries({ queryKey: ["assessment-local", ownerId] });
      setSubmissionState("pending");
      // Экран открывается сразу по условиям правил из knowledge/, до ответа сервера.
      const opensHelp = evaluateHelpTriggers(
        help?.triggers ?? [],
        definition.scale,
        payload.answers,
      );
      if (opensHelp) setHelpNotice(null);
      const outcome = await submissionOutcome(
        payload.submission_id,
        online,
        {
          syncNow,
          // Очередь закрыта (сменился владелец) — обещать врачу нечего.
          outboxState: () =>
            store
              .getOutboxForOwner(ownerId)
              .then((records) => records.find((r) => r.submission_id === payload.submission_id))
              .then((record) => record?.state ?? null)
              .catch(() => null),
        },
        { passes: opensHelp ? 2 : 1 },
      );
      if (outcome === "alert-raised") setHelpNotice("sent");
      else if (opensHelp) setHelpNotice(outcome === "queued" ? "queued" : null);
      if (outcome === "alert-raised" || outcome === "confirmed") {
        setSubmissionState("saved");
        await queryClient.invalidateQueries({ queryKey: ["assessment-history", ownerId] });
      }
      // Экран помощи открыт — поток ждёт «Вернуться к отметке»; иначе идём дальше сразу.
      if (opensHelp || outcome === "alert-raised") pendingFinishRef.current = outcome;
      else onFinished?.(outcome);
    } catch {
      setStorageError(true);
    } finally {
      setSubmitting(false);
    }
  };

  if (sessionRevalidating) {
    return (
      <main className="mx-auto max-w-lg px-4 py-8">
        <p role="status" className="text-sm text-ink-muted">
          Проверяем вход и владельца локальных ответов…
        </p>
      </main>
    );
  }
  if (definitionQuery.isPending) {
    return <p className="mx-auto max-w-lg px-4 py-8 text-sm text-ink-muted">Загружаем опросник…</p>;
  }
  if (definitionQuery.isError || !definition) {
    if (embedded) return null;
    return (
      <main className="mx-auto max-w-lg px-4 py-8">
        <Alert tone="error">
          Не удалось открыть опросник. Проверьте связь и попробуйте ещё раз.
        </Alert>
        <Link className="mt-4 inline-block text-sm underline" to="/">
          Вернуться к опросникам
        </Link>
      </main>
    );
  }
  if (!draftReady) {
    return (
      <p className="mx-auto max-w-lg px-4 py-8 text-sm text-ink-muted">
        Загружаем сохранённые ответы…
      </p>
    );
  }

  return (
    <>
      {helpNotice !== undefined && (
        <PatientHelpScreen
          notice={helpNotice}
          backLabel={embedded ? "Вернуться к отметке" : "Вернуться к опросу"}
          onClose={() => {
            setHelpNotice(undefined);
            const outcome = pendingFinishRef.current;
            pendingFinishRef.current = null;
            if (outcome) onFinished?.(outcome);
          }}
        />
      )}
      {!embedded && (
        <AppHeader
          persona="patient"
          product="Штиль"
          actions={
            <Link className="text-sm underline" to="/">
              Назад
            </Link>
          }
        />
      )}
      {!embedded && submissionState !== "idle" && helpNotice === undefined ? (
        <AssessmentSubmitted sent={submissionState === "saved"} />
      ) : (
        <>
          <main
            className={
              embedded
                ? "patient-assessment-embedded"
                : "mx-auto flex max-w-2xl flex-col gap-4 px-4 py-6"
            }
          >
            <Panel>
              <h1 className="text-xl font-semibold text-ink">{definition.scale.title}</h1>
              <p className="mt-2 text-sm text-ink-muted">{definition.scale.instruction}</p>
              {/* В отметке источников нет (кадр 17); на отдельном экране — остаются. */}
              {!embedded && <DefinitionSources sources={definition.sources} />}
            </Panel>
            <Panel title={embedded ? undefined : "Ваши ответы"}>
              {draftVersionMismatch && draftQuery.data && (
                <AssessmentDraftVersionRecovery
                  ownerId={ownerId}
                  scaleId={scaleId}
                  currentVersion={definition.scale.version}
                  draft={draftQuery.data}
                />
              )}
              {!embedded && (
                <p className="mb-4 text-sm text-ink-muted" aria-live="polite">
                  Отвечено: {Object.keys(answers).length} из {definition.scale.items.length}
                </p>
              )}
              {formError && (
                <Alert tone="error">Ответьте на все вопросы, чтобы отправить результат.</Alert>
              )}
              {storageError && (
                <Alert tone="error">
                  Не удалось сохранить ответ на устройстве. Не закрывайте страницу и попробуйте ещё
                  раз.
                </Alert>
              )}
              {conflict && (
                <div className="flex flex-col gap-2">
                  <Alert tone="error">
                    Ответы изменились в другой вкладке. Перезагрузите страницу, чтобы загрузить
                    актуальные ответы и продолжить.
                  </Alert>
                  <Button
                    className="self-start"
                    variant="secondary"
                    onClick={() => window.location.reload()}
                  >
                    Перезагрузить страницу
                  </Button>
                </div>
              )}
              <form onSubmit={submit} className="mt-4 flex flex-col gap-5" noValidate>
                {definition.scale.items.map((item) => {
                  const errorId = `missing-${item.id}`;
                  const optionOccurrences = new Map<string, number>();
                  return (
                    <fieldset
                      key={item.id}
                      aria-describedby={
                        formError && !Object.hasOwn(answers, item.id) ? errorId : undefined
                      }
                      className="min-w-0 border-t border-line pt-4 first:border-0 first:pt-0"
                    >
                      <legend className="mb-3 text-base font-medium text-ink">{item.text}</legend>
                      <div className="flex flex-col gap-2">
                        {item.options.map((option) => {
                          const identity = JSON.stringify([item.id, option.value, option.text]);
                          const occurrence = optionOccurrences.get(identity) ?? 0;
                          optionOccurrences.set(identity, occurrence + 1);
                          const optionId = `answer-${encodeURIComponent(identity)}${
                            occurrence > 0 ? `-${occurrence}` : ""
                          }`;

                          return (
                            <label
                              key={optionId}
                              htmlFor={optionId}
                              className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-primary"
                            >
                              <input
                                id={optionId}
                                ref={(element) => {
                                  if (option === item.options[0] && element)
                                    questionRefs.current.set(item.id, element);
                                }}
                                type="radio"
                                name={`answer-${item.id}`}
                                value={option.value}
                                disabled={draftVersionMismatch || conflict}
                                checked={answers[item.id] === option.value}
                                onChange={() =>
                                  saveAnswers({ ...answersRef.current, [item.id]: option.value })
                                }
                                className="size-5 shrink-0 accent-primary"
                              />
                              <span>{option.text}</span>
                            </label>
                          );
                        })}
                      </div>
                      {formError && !Object.hasOwn(answers, item.id) && (
                        <span id={errorId} className="mt-2 block text-sm font-medium text-danger">
                          Выберите ответ.
                        </span>
                      )}
                    </fieldset>
                  );
                })}
                <p role="status" aria-live="polite" className="min-h-5 text-sm text-ink-muted">
                  {saveState === "saving" && "Сохраняем ответы на устройстве…"}
                  {saveState === "saved" && "Ответы сохранены на устройстве."}
                </p>
                <Button
                  type="submit"
                  disabled={submitting || draftQuery.isPending || draftVersionMismatch || conflict}
                >
                  {submitting ? "Сохраняем…" : embedded ? "Дальше" : "Отправить ответы"}
                </Button>
              </form>
            </Panel>
          </main>
        </>
      )}
    </>
  );
}

/** После отправки на отдельном экране (P2-17): без пустой формы, с выходом на главную. */
function AssessmentSubmitted({ sent }: { sent: boolean }) {
  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-4 px-4 py-6">
      <Panel>
        <h1 className="text-xl font-semibold text-ink">
          {sent ? "Ответы отправлены" : "Ответы сохранены на устройстве"}
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          {sent
            ? "Результат сохранён на сервере. Врач увидит его сегодня."
            : "Сохранено на устройстве. Отправим, когда появится связь."}
        </p>
        <Link
          to="/"
          className="mt-4 inline-flex min-h-11 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          На главную
        </Link>
      </Panel>
    </main>
  );
}
