import { QueryClient } from "@tanstack/react-query";
import { expect, test } from "vitest";
import { DIARY_ENTRIES, DIARY_MOODS, refreshDiary } from "./diaryQueries";
import { PATIENT_PROPOSALS } from "./queries";

const QUERY = { start: "2026-10-06", end: "2026-10-19" };
const key = (path: string, patientId: string) => [
  "get",
  path,
  { params: { path: { patient_id: patientId }, query: QUERY } },
];

function seeded(): QueryClient {
  const client = new QueryClient();
  for (const patientId of ["p1", "p2"]) {
    client.setQueryData(key(DIARY_MOODS, patientId), { items: [] });
    client.setQueryData([...key(DIARY_ENTRIES, patientId), "pages"], { pages: [], pageParams: [] });
    client.setQueryData(
      ["get", PATIENT_PROPOSALS, { params: { path: { patient_id: patientId } } }],
      [],
    );
  }
  return client;
}

const invalidated = (client: QueryClient) =>
  client
    .getQueryCache()
    .getAll()
    .filter((q) => q.state.isInvalidated)
    .map((q) => q.queryKey.slice(1, 3));

// Спека #115, раздел 4: `diary.changed` обновляет записи и отметки этого пациента.
test("refreshDiary invalidates entries and moods of the patient only", async () => {
  const client = seeded();
  await refreshDiary(client, "p1");
  expect(invalidated(client)).toEqual([
    key(DIARY_MOODS, "p1").slice(1),
    key(DIARY_ENTRIES, "p1").slice(1),
  ]);
});

test("after a reconnect every diary is read again", async () => {
  const client = seeded();
  await refreshDiary(client, null);
  expect(invalidated(client)).toHaveLength(4);
});
