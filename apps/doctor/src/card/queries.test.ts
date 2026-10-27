import { $api } from "@compass/api-client";
import { QueryClient } from "@tanstack/react-query";
import { expect, test } from "vitest";
import {
  PATIENT_GUIDANCE,
  PATIENT_REACTIONS,
  PATIENT_SCHEDULE,
  PATIENT_SUMMARY,
  QUEUE,
  QUEUE_SUMMARY,
  refreshAlerts,
  refreshAssessments,
  refreshProposals,
  refreshSummary,
  refreshTherapy,
  refreshUnread,
} from "./queries";
import { storeSummary } from "./visitSummary";

test("refreshUnread invalidates unread message counts", async () => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", "/api/v1/messaging/unread");
  client.setQueryData(queryKey, []);
  await refreshUnread(client);
  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});

// Спека #107, раздел 6: после правки профиля или диагнозов панель сразу показывает
// «считается» или «устарело», поэтому `refreshTherapy` обновляет и справку.
test("refreshTherapy also refreshes guideline options", async () => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", PATIENT_GUIDANCE, {
    params: { path: { patient_id: "p1" } },
  });
  client.setQueryData(queryKey, {
    status: "done",
    stale: false,
    claims: [],
    computed_at: null,
    guidelines: [],
    catalog_drugs: [],
  });

  await refreshTherapy(client);

  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});

// Спека #29: отметка реакции пациентом приходит событием сигнала, и список отметок
// у назначения обновляется вместе с сигналом, без перезагрузки.
test("refreshAlerts also refreshes reaction reports", async () => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", PATIENT_REACTIONS, {
    params: { path: { patient_id: "p1" } },
  });
  client.setQueryData(queryKey, []);

  await refreshAlerts(client);

  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});

// Спека #111, правило 4: поздний ответ закрывает пропуск, поэтому новый ответ
// обновляет и расписание — иначе на графике остался бы закрытый пропуск.
test("refreshAssessments also refreshes the scale schedule", async () => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", PATIENT_SCHEDULE, {
    params: { path: { patient_id: "p1" } },
  });
  client.setQueryData(queryKey, { today: "2026-10-19", items: [] });

  await refreshAssessments(client);

  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});

// Спека #96, 3.5: после решения в любом месте кабинета обновляются очередь врача
// и счётчик «Предложения» в меню.
test.each([
  ["refreshProposals", refreshProposals],
  ["refreshTherapy", refreshTherapy],
])("%s refreshes the doctor's queue and its summary", async (_, refresh) => {
  const client = new QueryClient();
  const queue = ["get", QUEUE, { params: { query: { status: "proposed" } } }];
  const { queryKey: summary } = $api.queryOptions("get", QUEUE_SUMMARY, {
    params: { query: { since: "2026-10-22T21:00:00.000Z" } },
  });
  client.setQueryData(queue, { pages: [], pageParams: [] });
  client.setQueryData(summary, { pending: 1, decided_today: 0 });

  await refresh(client);

  expect(client.getQueryState(queue)?.isInvalidated).toBe(true);
  expect(client.getQueryState(summary)?.isInvalidated).toBe(true);
});

// Спека #38, раздел 3.6: ответ подтверждения и повтора сразу показывается в группе сводки,
// без лишнего запроса; после 409 сводка запрашивается заново.
test("storeSummary puts the response where the overview reads the summary", () => {
  const client = new QueryClient();
  const summary = { id: "s1", status: "confirmed" } as never;
  const { queryKey } = $api.queryOptions("get", PATIENT_SUMMARY, {
    params: { path: { patient_id: "p1" } },
  });

  storeSummary(client, "p1", summary);

  expect(client.getQueryData(queryKey)).toBe(summary);
});

test("refreshSummary refetches the summary", async () => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", PATIENT_SUMMARY, {
    params: { path: { patient_id: "p1" } },
  });
  client.setQueryData(queryKey, { id: "s1", status: "done" } as never);

  await refreshSummary(client);

  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});

// Спека #38: решение врача по сигналу или предложению и события realtime меняют факты
// периода. Без нового запроса «Подтвердить сводку» получил бы 409 `stale`.
test.each([
  ["refreshAlerts", refreshAlerts],
  ["refreshAssessments", refreshAssessments],
  ["refreshProposals", refreshProposals],
  ["refreshTherapy", refreshTherapy],
])("%s also refreshes the visit summary", async (_, refresh) => {
  const client = new QueryClient();
  const { queryKey } = $api.queryOptions("get", PATIENT_SUMMARY, {
    params: { path: { patient_id: "p1" } },
  });
  client.setQueryData(queryKey, { id: "s1", status: "done" } as never);

  await refresh(client);

  expect(client.getQueryState(queryKey)?.isInvalidated).toBe(true);
});
