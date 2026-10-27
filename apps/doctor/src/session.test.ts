import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@compass/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@compass/api-client")>()),
  fetchClient: {},
  fetchCurrentUser: vi.fn(),
}));

import { fetchCurrentUser } from "@compass/api-client";
import { currentUser, homeLocation, isSessionUnavailable } from "./session";

const fetchCurrentUserMock = vi.mocked(fetchCurrentUser);

afterEach(() => fetchCurrentUserMock.mockReset());

describe("doctor session check", () => {
  it("clears cached account data after auth/me confirms there is no session", async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["patients"], [{ id: "private-patient" }]);
    queryClient.getMutationCache().build(queryClient, { mutationFn: async () => "pending" });
    fetchCurrentUserMock.mockResolvedValue(null);

    await expect(currentUser(queryClient)).resolves.toBeNull();

    expect(queryClient.getQueryCache().getAll()).toEqual([]);
    expect(queryClient.getMutationCache().getAll()).toEqual([]);
  });
});

describe("doctor session check without network (D1-03)", () => {
  it("keeps cached data and reports an unavailable session instead of signing out", async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(["patients"], [{ id: "private-patient" }]);
    fetchCurrentUserMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(currentUser(queryClient)).rejects.toSatisfy(isSessionUnavailable);

    expect(queryClient.getQueryData(["patients"])).toEqual([{ id: "private-patient" }]);
  });

  it("returns the cached user without a request while the cache holds a session", async () => {
    const queryClient = new QueryClient();
    const user = { id: "d1", email: "d@example.com", role: "doctor", full_name: "Врач" };
    queryClient.setQueryData(["me"], user);

    await expect(currentUser(queryClient)).resolves.toEqual(user);
    expect(fetchCurrentUserMock).not.toHaveBeenCalled();
  });

  it("passes a server error through unchanged", async () => {
    const queryClient = new QueryClient();
    fetchCurrentUserMock.mockRejectedValue(new Error("GET /api/v1/auth/me failed: 500"));

    await expect(currentUser(queryClient)).rejects.toSatisfy(isSessionUnavailable);
  });
});

describe("start page of a signed-in user", () => {
  it.each([
    ["doctor", "/doctor/", false],
    ["admin", "/doctor/admin", false],
    ["patient", "/patient/", true],
  ] as const)("%s goes to %s, full page load: %s", (role, href, reloadDocument) => {
    expect(homeLocation(role)).toEqual({ href, reloadDocument, replace: true });
  });
});
