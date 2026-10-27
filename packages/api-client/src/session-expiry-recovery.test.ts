import { QueryClient } from "@tanstack/react-query";
import createFetchClient from "openapi-fetch";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installSessionExpiryHandler, isSessionExpiryPending } from "./auth";
import type { paths } from "./schema";

describe("запросы после истечения сессии", () => {
  const uninstallers: Array<() => void> = [];

  afterEach(() => {
    for (const uninstall of uninstallers.splice(0)) uninstall();
  });

  it("allows a later session to expire after a successful login", async () => {
    let status = 401;
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async () =>
        new Response(JSON.stringify({ status, title: "Synthetic" }), {
          status,
          headers: { "content-type": "application/problem+json" },
        }),
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await client.GET("/api/v1/assessments/scales");
    await vi.waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
    status = 200;
    await client.POST("/api/v1/auth/login", { body: { email: "x", password: "x" } });
    status = 401;
    await client.GET("/api/v1/assessments/scales");
    await vi.waitFor(() => expect(onExpired).toHaveBeenCalledTimes(2));
  });

  it("stops protected requests after 401 until a successful login", async () => {
    let status = 401;
    const requestedPaths: string[] = [];
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async (request) => {
        requestedPaths.push(new URL(request.url).pathname);
        return new Response(JSON.stringify({ status, title: "Synthetic" }), {
          status,
          headers: { "content-type": "application/problem+json" },
        });
      },
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await client.GET("/api/v1/patients");
    expect(isSessionExpiryPending(client)).toBe(true);
    await client.GET("/api/v1/patients");
    expect(requestedPaths).toEqual(["/api/v1/patients"]);
    expect(onExpired).toHaveBeenCalledTimes(1);

    status = 200;
    await client.POST("/api/v1/auth/login", { body: { email: "x", password: "x" } });
    expect(isSessionExpiryPending(client)).toBe(false);
    await client.GET("/api/v1/patients");
    expect(requestedPaths).toEqual(["/api/v1/patients", "/api/v1/auth/login", "/api/v1/patients"]);
  });

  it("lets the consent document and a patient registration through while protected requests stay blocked", async () => {
    const requestedPaths: string[] = [];
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        requestedPaths.push(path);
        const status =
          path === "/api/v1/patients/register"
            ? 201
            : path === "/api/v1/patients/consent"
              ? 200
              : 401;
        return new Response(JSON.stringify({ status, title: "Synthetic" }), {
          status,
          headers: { "content-type": "application/problem+json" },
        });
      },
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await client.GET("/api/v1/patients");
    expect(isSessionExpiryPending(client)).toBe(true);
    const consent = await client.GET("/api/v1/patients/consent");
    const { response } = await client.POST("/api/v1/patients/register", {
      body: {
        invite_token: "synthetic-invite",
        full_name: "Синтетический пациент",
        email: "patient@example.invalid",
        password: "synthetic-password",
        consent_personal_data: true,
        consent_version: "synthetic-version",
      },
    });
    await client.GET("/api/v1/patients");

    expect(consent.response.status).toBe(200);
    expect(response.status).toBe(201);
    expect(requestedPaths).toEqual([
      "/api/v1/patients",
      "/api/v1/patients/consent",
      "/api/v1/patients/register",
    ]);
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("reopens protected requests after auth/me confirms a new session", async () => {
    let authenticated = false;
    let sessionCheckStatus = 401;
    const requestedPaths: string[] = [];
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        requestedPaths.push(path);
        const status = path === "/api/v1/auth/me" ? sessionCheckStatus : authenticated ? 200 : 401;
        return new Response(JSON.stringify({ status, title: "Synthetic" }), {
          status,
          headers: { "content-type": "application/problem+json" },
        });
      },
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    await client.GET("/api/v1/patients");
    expect(isSessionExpiryPending(client)).toBe(true);
    await client.GET("/api/v1/auth/me");
    expect(isSessionExpiryPending(client)).toBe(true);
    authenticated = true;
    sessionCheckStatus = 200;
    await client.GET("/api/v1/auth/me");

    expect(isSessionExpiryPending(client)).toBe(false);
    await client.GET("/api/v1/patients");
    expect(requestedPaths).toEqual([
      "/api/v1/patients",
      "/api/v1/auth/me",
      "/api/v1/auth/me",
      "/api/v1/patients",
    ]);
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("does not restore an expired session from an auth/me request started before expiry", async () => {
    let releaseSessionCheck: (response: Response) => void = () => {};
    let signalSessionCheckStarted: () => void = () => {};
    const sessionCheckStarted = new Promise<void>((resolve) => {
      signalSessionCheckStarted = resolve;
    });
    const delayedSessionResponse = new Promise<Response>((resolve) => {
      releaseSessionCheck = resolve;
    });
    const requestedPaths: string[] = [];
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        requestedPaths.push(path);
        if (path === "/api/v1/auth/me") {
          signalSessionCheckStarted();
          return delayedSessionResponse;
        }
        return new Response('{"status":401}', {
          status: 401,
          headers: { "content-type": "application/problem+json" },
        });
      },
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    const oldSessionCheck = client.GET("/api/v1/auth/me");
    await sessionCheckStarted;
    await client.GET("/api/v1/patients");
    releaseSessionCheck(
      new Response('{"id":"old-session-user"}', {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    await oldSessionCheck;
    await client.GET("/api/v1/patients");

    expect(isSessionExpiryPending(client)).toBe(true);
    expect(requestedPaths).toEqual(["/api/v1/auth/me", "/api/v1/patients"]);
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it("ignores an old mutation 401 that arrives after the new login succeeds", async () => {
    let releaseOldMutation: (response: Response) => void = () => {};
    let signalOldMutationStarted: () => void = () => {};
    const oldMutationStarted = new Promise<void>((resolve) => {
      signalOldMutationStarted = resolve;
    });
    const oldMutationResponse = new Promise<Response>((resolve) => {
      releaseOldMutation = resolve;
    });
    const client = createFetchClient<paths>({
      baseUrl: "http://test",
      fetch: async (request) => {
        const path = new URL(request.url).pathname;
        if (path === "/api/v1/auth/logout") {
          signalOldMutationStarted();
          return oldMutationResponse;
        }
        const status = path === "/api/v1/auth/login" ? 200 : 401;
        return new Response(JSON.stringify({ status, title: "Synthetic" }), {
          status,
          headers: { "content-type": "application/problem+json" },
        });
      },
    });
    const queryClient = new QueryClient();
    const onExpired = vi.fn();
    uninstallers.push(
      installSessionExpiryHandler(client, { queryClient, onExpired, isLoginPage: () => false }),
    );

    const oldMutation = client.POST("/api/v1/auth/logout");
    await oldMutationStarted;
    await client.GET("/api/v1/patients");
    expect(isSessionExpiryPending(client)).toBe(true);
    await client.POST("/api/v1/auth/login", { body: { email: "new", password: "secret" } });
    expect(isSessionExpiryPending(client)).toBe(false);
    releaseOldMutation(
      new Response('{"status":401}', {
        status: 401,
        headers: { "content-type": "application/problem+json" },
      }),
    );
    await oldMutation;

    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(isSessionExpiryPending(client)).toBe(false);
  });
});
