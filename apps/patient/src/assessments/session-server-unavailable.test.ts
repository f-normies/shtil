import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFINITION_V1, PATIENT_A } from "../../e2e/assessment-fixtures";
import type { DefinitionSnapshot, OwnerPointer } from "./types";

const mocks = vi.hoisted(() => {
  const local: { pointer: OwnerPointer | null; definitions: DefinitionSnapshot[] } = {
    pointer: null,
    definitions: [],
  };
  const store = {
    getOwnerPointer: vi.fn(async () => (local.pointer ? { ...local.pointer } : null)),
    getCachedDefinitions: vi.fn(async () => [...local.definitions]),
    lockOwnerPointer: vi.fn(async () => {}),
    rememberOnlinePatientIfUnchanged: vi.fn(async () => true),
  };
  return { local, store, getMe: vi.fn() };
});

// Keep the real client helpers; replace only the HTTP transport of the shared client.
vi.mock("@compass/api-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@compass/api-client")>()),
  fetchClient: { GET: mocks.getMe },
}));

vi.mock("./store", () => ({
  AssessmentStore: { open: vi.fn(async () => mocks.store) },
}));

import { requirePatientSession } from "../diary/route-guard";
import { resolvePatientSession } from "../session";

/** The result shape openapi-fetch returns for a non-2xx reply. */
function failedReply(status: number, body: string) {
  return { error: body, response: new Response(body, { status }) };
}

const SERVER_FAILURES = [
  ["502 from the proxy", () => Promise.resolve(failedReply(502, "<html>Bad Gateway</html>"))],
  ["503 problem reply", () => Promise.resolve(failedReply(503, '{"status":503}'))],
  // openapi-fetch parses a 2xx body as JSON and throws SyntaxError for an HTML page.
  ["200 with a non-JSON body", () => Promise.reject(new SyntaxError("Unexpected token '<'"))],
  [
    "200 with an empty body",
    () => Promise.resolve({ data: undefined, response: new Response(null, { status: 200 }) }),
  ],
] as const;

describe("session check when the server is unavailable", () => {
  beforeEach(() => {
    mocks.getMe.mockReset();
    mocks.local.pointer = null;
    mocks.local.definitions = [];
  });

  it.each(SERVER_FAILURES)("%s opens the saved answers offline", async (_case, reply) => {
    mocks.local.pointer = { id: PATIENT_A, role: "patient", locked: false };
    mocks.local.definitions = [DEFINITION_V1];
    mocks.getMe.mockImplementationOnce(reply);

    await expect(resolvePatientSession(new QueryClient())).resolves.toEqual({
      status: "offline",
      ownerId: PATIENT_A,
      cause: "server",
    });
  });

  it.each(SERVER_FAILURES)("%s without saved data reports the server", async (_case, reply) => {
    mocks.getMe.mockImplementationOnce(reply);

    await expect(resolvePatientSession(new QueryClient())).resolves.toEqual({
      status: "network-unavailable",
      cause: "server",
    });
  });

  it("a failed network request keeps the network cause", async () => {
    mocks.local.pointer = { id: PATIENT_A, role: "patient", locked: false };
    mocks.local.definitions = [DEFINITION_V1];
    mocks.getMe.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(resolvePatientSession(new QueryClient())).resolves.toEqual({
      status: "offline",
      ownerId: PATIENT_A,
      cause: "network",
    });
  });

  it("allows the diary route to open for its saved owner without cached scales", async () => {
    mocks.local.pointer = { id: PATIENT_A, role: "patient", locked: false };
    mocks.getMe.mockRejectedValueOnce(new TypeError("Failed to fetch"));

    await expect(requirePatientSession(new QueryClient())).resolves.toEqual({
      session: { status: "offline", ownerId: PATIENT_A, cause: "network" },
    });
  });

  it("an unexpected client error still fails the route", async () => {
    mocks.getMe.mockResolvedValueOnce(failedReply(404, '{"status":404}'));

    await expect(resolvePatientSession(new QueryClient())).rejects.toThrow("404");
  });
});
