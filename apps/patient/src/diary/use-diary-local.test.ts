import { onlineManager, QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listEntries: vi.fn(async () => []),
  listDrafts: vi.fn(async () => []),
  getOutboxForOwner: vi.fn(async () => []),
  getCachedDefinitions: vi.fn(async () => []),
}));

vi.mock("./queries", () => ({
  getDiaryStore: async () => ({
    listEntries: mocks.listEntries,
    listDrafts: mocks.listDrafts,
    getOutboxForOwner: mocks.getOutboxForOwner,
  }),
}));

vi.mock("../session", () => ({
  getAssessmentStore: async () => ({ getCachedDefinitions: mocks.getCachedDefinitions }),
}));

import { diaryDraftsQueryOptions } from "./use-diary-local";

describe("diary local queries", () => {
  afterEach(() => {
    onlineManager.setOnline(true);
    vi.clearAllMocks();
  });

  it("reads a saved draft when the browser is offline", async () => {
    onlineManager.setOnline(false);
    const client = new QueryClient();
    const observer = new QueryObserver(client, diaryDraftsQueryOptions("patient-a"));
    const unsubscribe = observer.subscribe(() => {});

    try {
      await vi.waitFor(() => {
        expect(observer.getCurrentResult()).toMatchObject({
          status: "success",
          fetchStatus: "idle",
        });
      });
      expect(mocks.listDrafts).toHaveBeenCalledWith("patient-a");
    } finally {
      unsubscribe();
      client.clear();
    }
  });
});
