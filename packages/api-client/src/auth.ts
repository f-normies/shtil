import type { QueryClient } from "@tanstack/react-query";
import type { Client, Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

type User = components["schemas"]["UserOut"];
type Role = User["role"];
const expiringClients = new WeakSet<object>();
const sessionGenerations = new WeakMap<object, number>();
const expiryTriggers = new WeakMap<object, () => Promise<void>>();
// These requests do not depend on the current session: they stay open while the app moves to login.
const SESSIONLESS_PATHS = [
  "/api/v1/auth/login",
  "/api/v1/auth/me",
  "/api/v1/patients/consent",
  "/api/v1/patients/register",
];

/** Стартовый адрес для конкретной роли. */
export function homePathFor(role: Role): "/doctor/" | "/patient/" | "/doctor/admin" {
  if (role === "patient") return "/patient/";
  if (role === "admin") return "/doctor/admin";
  return "/doctor/";
}

type SessionExpiryOptions = {
  queryClient: QueryClient;
  onExpired: () => void | Promise<void>;
  isLoginPage: () => boolean;
};

/** Install one response handler that clears in-memory user data on an expired session. */
export function installSessionExpiryHandler(
  client: Client<paths>,
  options: SessionExpiryOptions,
): () => void {
  let expirationStarted = false;
  let sessionGeneration = sessionGenerations.get(client) ?? 0;
  sessionGenerations.set(client, sessionGeneration);
  const requestGenerations = new WeakMap<Request, number>();
  const advanceSessionGeneration = () => {
    sessionGeneration += 1;
    sessionGenerations.set(client, sessionGeneration);
  };
  const expire = async () => {
    if (expirationStarted) return;
    expirationStarted = true;
    advanceSessionGeneration();
    expiringClients.add(client);
    await options.queryClient.cancelQueries();
    options.queryClient.clear();
    await options.onExpired();
  };
  const middleware: Middleware = {
    onRequest({ request }) {
      requestGenerations.set(request, sessionGeneration);
      const path = new URL(request.url).pathname;
      if (expirationStarted && !isSessionless(path)) {
        return new Response(JSON.stringify({ status: 401, title: "Сессия завершена" }), {
          status: 401,
          headers: { "content-type": "application/problem+json" },
        });
      }
    },
    async onResponse({ request, response }) {
      const path = new URL(request.url).pathname;
      const requestGeneration = requestGenerations.get(request);
      if (requestGeneration === undefined) return;
      const isCurrentGeneration = requestGeneration === sessionGeneration;
      if (isLogin(path) && response.ok && isCurrentGeneration) {
        advanceSessionGeneration();
        expirationStarted = false;
        expiringClients.delete(client);
        return;
      }
      if (isSessionCheck(path) && response.ok && expirationStarted && isCurrentGeneration) {
        advanceSessionGeneration();
        expirationStarted = false;
        expiringClients.delete(client);
        return;
      }
      if (
        response.status !== 401 ||
        isSessionless(path) ||
        !isCurrentGeneration ||
        options.isLoginPage()
      ) {
        return;
      }
      await expire();
    },
  };
  client.use(middleware);
  expiryTriggers.set(client, expire);
  return () => {
    client.eject(middleware);
    expiringClients.delete(client);
    if (expiryTriggers.get(client) === expire) expiryTriggers.delete(client);
  };
}

/**
 * Start the same single transition to login when the session end arrives outside HTTP,
 * for example as a WebSocket close code. Does nothing if the handler is not installed.
 */
export async function expireSession(client: Client<paths>): Promise<void> {
  await expiryTriggers.get(client)?.();
}

/** Whether this client's app-wide transition to the login screen has started. */
export function isSessionExpiryPending(client: Client<paths>): boolean {
  return expiringClients.has(client);
}

/** Identifies the confirmed login state so callers can discard stale async results. */
export function getSessionGeneration(client: Client<paths>): number {
  return sessionGenerations.get(client) ?? 0;
}

function isSessionless(path: string): boolean {
  return SESSIONLESS_PATHS.some((sessionless) => path.endsWith(sessionless));
}

function isLogin(path: string): boolean {
  return path.endsWith("/api/v1/auth/login");
}

function isSessionCheck(path: string): boolean {
  return path.endsWith("/api/v1/auth/me");
}

/** Preserve the configured single retry for transient errors while avoiding retries after 401. */
export function shouldRetryApiQuery(failureCount: number, error: unknown): boolean {
  if (responseStatus(error) === 401) return false;
  return failureCount < 1;
}

function responseStatus(error: unknown): number | undefined {
  if (error instanceof Response) return error.status;
  if (!error || typeof error !== "object") return undefined;
  if ("status" in error && typeof error.status === "number") return error.status;
  if ("response" in error && error.response instanceof Response) return error.response.status;
  return undefined;
}

/**
 * Текущий пользователь или null, если сессии нет.
 * Ошибка сервера пробрасывается: сбой не должен выглядеть как выход из системы.
 */
export async function fetchCurrentUser(client: Client<paths>): Promise<User | null> {
  const { data, response } = await client.GET("/api/v1/auth/me");
  if (response.status === 401) {
    return null;
  }
  if (!data) {
    throw new Error(`GET /api/v1/auth/me failed: ${response.status}`);
  }
  return data;
}
