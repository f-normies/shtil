import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  expireSession,
  installSessionExpiryHandler,
  isSessionExpiryPending,
  shouldRetryApiQuery,
} from "./auth";
import type { paths } from "./schema";

function responseClient(status: number, onFetch = vi.fn()) {
  return createFetchClient<paths>({
    baseUrl: "http://test",
    fetch: async () => {
      onFetch();
      return new Response(JSON.stringify({ status, title: "Synthetic" }), {
        status,
        headers: { "content-type": "application/problem+json" },
      });
    },
  });
}

describe("общий обработчик истечения сессии", () => {
  const uninstallers: Array<() => void> = [];

  afterEach(() => {
    for (const uninstall of uninstallers.splice(0)) uninstall();
  });

  it("one 401 from a protected read or write runs the expiry callback once", async () => {
    const client = responseClient(401);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["patient", "private"], { id: "synthetic-patient" });
    const mutation = queryClient.getMutationCache().build(queryClient, {
      mutationFn: async () => "saved",
    });
    await mutation.execute(undefined);
    let signal: AbortSignal | undefined;
    let startQuery: () => void = () => {};
    const queryStarted = new Promise<void>((resolve) => {
      startQuery = resolve;
    });
    const pendingQuery = queryClient
      .fetchQuery({
        queryKey: ["patient", "pending"],
        queryFn: ({ signal: querySignal }) => {
          signal = querySignal;
          startQuery();
          return new Promise<string>(() => {});
        },
      })
      .catch(() => undefined);
    await queryStarted;
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await Promise.all([
      client.GET("/api/v1/assessments/scales"),
      client.POST("/api/v1/auth/logout"),
    ]);
    await pendingQuery;

    await vi.waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
    expect(signal?.aborted).toBe(true);
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
    expect(queryClient.getMutationCache().getAll()).toEqual([]);
  });

  it("leaves login failures, login-page probes, 403, 5xx, and network failures alone", async () => {
    const onExpired = vi.fn();
    const queryClient = new QueryClient();
    const loginClient = responseClient(401);
    uninstallers.push(
      installSessionExpiryHandler(loginClient, {
        queryClient,
        onExpired,
        isLoginPage: () => false,
      }),
    );
    await loginClient.POST("/api/v1/auth/login", { body: { email: "x", password: "x" } });

    const onLoginPageClient = responseClient(401);
    uninstallers.push(
      installSessionExpiryHandler(onLoginPageClient, {
        queryClient,
        onExpired,
        isLoginPage: () => true,
      }),
    );
    await onLoginPageClient.GET("/api/v1/assessments/scales");

    const sessionProbeClient = responseClient(401);
    uninstallers.push(
      installSessionExpiryHandler(sessionProbeClient, {
        queryClient,
        onExpired,
        isLoginPage: () => false,
      }),
    );
    await sessionProbeClient.GET("/api/v1/auth/me");

    const forbiddenClient = responseClient(403);
    uninstallers.push(
      installSessionExpiryHandler(forbiddenClient, {
        queryClient,
        onExpired,
        isLoginPage: () => false,
      }),
    );
    await forbiddenClient.GET("/api/v1/assessments/scales");

    const unavailableClient = responseClient(503);
    uninstallers.push(
      installSessionExpiryHandler(unavailableClient, {
        queryClient,
        onExpired,
        isLoginPage: () => false,
      }),
    );
    await unavailableClient.GET("/api/v1/assessments/scales");

    const offlineClient = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async () => {
        throw new TypeError("offline");
      },
    });
    uninstallers.push(
      installSessionExpiryHandler(offlineClient, {
        queryClient,
        onExpired,
        isLoginPage: () => false,
      }),
    );
    await expect(offlineClient.GET("/api/v1/assessments/scales")).rejects.toThrow("offline");
    expect(onExpired).not.toHaveBeenCalled();
  });

  it("does not retry an unauthorized API query while retaining one retry for other errors", () => {
    expect(shouldRetryApiQuery(0, { status: 401, title: "Unauthorized" })).toBe(false);
    expect(shouldRetryApiQuery(0, { status: 403, title: "Forbidden" })).toBe(true);
    expect(shouldRetryApiQuery(0, { status: 503, title: "Unavailable" })).toBe(true);
    expect(shouldRetryApiQuery(1, { status: 503, title: "Unavailable" })).toBe(false);
  });

  it("a session end reported outside HTTP runs the same single transition", async () => {
    const onFetch = vi.fn();
    const client = responseClient(200, onFetch);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(["patients"], [{ id: "synthetic-patient" }]);
    let signal: AbortSignal | undefined;
    let startQuery: () => void = () => {};
    const queryStarted = new Promise<void>((resolve) => {
      startQuery = resolve;
    });
    const pendingQuery = queryClient
      .fetchQuery({
        queryKey: ["alerts"],
        queryFn: ({ signal: querySignal }) => {
          signal = querySignal;
          startQuery();
          return new Promise<string>(() => {});
        },
      })
      .catch(() => undefined);
    await queryStarted;
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await Promise.all([expireSession(client), expireSession(client)]);
    await pendingQuery;
    await client.GET("/api/v1/patients");

    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(true);
    expect(queryClient.getQueryCache().getAll()).toEqual([]);
    expect(isSessionExpiryPending(client)).toBe(true);
    expect(onFetch).not.toHaveBeenCalled();
  });
});
