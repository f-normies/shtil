import type { Schemas } from "@compass/api-client";
import { QueryClient } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { PATIENT_A, PATIENT_B } from "../../e2e/assessment-fixtures";
import type { OwnerPointer } from "./types";

const mocks = vi.hoisted(() => {
  let pointer: { id: string; role: "patient"; locked: boolean } | null = {
    id: "00000000-0000-4000-8000-0000000000b1",
    role: "patient",
    locked: false,
  };
  const store = {
    getOwnerPointer: vi.fn(async () => (pointer ? { ...pointer } : null)),
    rememberOnlinePatient: vi.fn(async (patient: { id: string; role: "patient" }) => {
      pointer = { id: patient.id, role: "patient", locked: false };
    }),
    rememberOnlinePatientIfUnchanged: vi.fn(
      async (
        patient: { id: string; role: "patient" },
        expected: { id: string; role: "patient"; locked: boolean } | null,
      ) => {
        const unchanged =
          pointer?.id === expected?.id &&
          pointer?.role === expected?.role &&
          pointer?.locked === expected?.locked;
        if (!unchanged) return false;
        pointer = { id: patient.id, role: "patient", locked: false };
        return true;
      },
    ),
    lockOwnerPointer: vi.fn(async (ownerId: string) => {
      if (pointer?.id === ownerId) pointer = { ...pointer, locked: true };
    }),
    getCachedDefinitions: vi.fn(async () => []),
  };
  return {
    store,
    openStore: vi.fn(async () => store),
    fetchCurrentUser: vi.fn(),
    messaging: {
      confirmMessagingOwner: vi.fn(),
      lockMessagingAccess: vi.fn(),
    },
    readPointer: () => (pointer ? { ...pointer } : null),
    setPointer: (next: { id: string; role: "patient"; locked: boolean } | null) => {
      pointer = next ? { ...next } : null;
    },
  };
});

vi.mock("@compass/api-client", () => ({
  // The session check reads /auth/me through the shared client; answer with the mocked user.
  fetchClient: {
    GET: async () => {
      const user = await mocks.fetchCurrentUser();
      return user
        ? { data: user, response: new Response(null, { status: 200 }) }
        : { error: undefined, response: new Response(null, { status: 401 }) };
    },
  },
  fetchCurrentUser: mocks.fetchCurrentUser,
}));

vi.mock("./store", () => ({
  AssessmentStore: { open: mocks.openStore },
}));

vi.mock("@compass/ui", () => mocks.messaging);

import { ME_KEY, resolvePatientSession } from "../session";

it("does not publish a late session response after another tab changes the owner", async () => {
  mocks.messaging.confirmMessagingOwner.mockClear();
  mocks.messaging.lockMessagingAccess.mockClear();
  const userA = {
    id: PATIENT_A,
    role: "patient",
    full_name: "Synthetic patient A",
    email: "patient-a@example.invalid",
  } satisfies Schemas["UserOut"];
  const userB = {
    id: PATIENT_B,
    role: "patient",
    full_name: "Synthetic patient B",
    email: "patient-b@example.invalid",
  } satisfies Schemas["UserOut"];
  const cachedQueries = new Map<string, unknown>();
  const queryClientMock = {
    cancelQueries: vi.fn(async () => {}),
    clear: vi.fn(() => cachedQueries.clear()),
    setQueryData(queryKey: readonly unknown[], value: unknown): unknown {
      cachedQueries.set(JSON.stringify(queryKey), value);
      return value;
    },
    getQueryData(queryKey: readonly unknown[]): unknown {
      return cachedQueries.get(JSON.stringify(queryKey));
    },
  };
  const queryClient = queryClientMock as unknown as QueryClient;
  let meCacheAtOwnerCheck: unknown;
  mocks.store.rememberOnlinePatientIfUnchanged.mockImplementationOnce(
    async (patient: { id: string; role: "patient" }, expected: OwnerPointer | null) => {
      meCacheAtOwnerCheck = queryClientMock.getQueryData(ME_KEY);
      const pointer = mocks.readPointer();
      const unchanged =
        pointer?.id === expected?.id &&
        pointer?.role === expected?.role &&
        pointer?.locked === expected?.locked;
      if (!unchanged) return false;
      mocks.setPointer({ id: patient.id, role: "patient", locked: false });
      return true;
    },
  );

  let releaseUserB: (user: Schemas["UserOut"] | null) => void = () => {};
  let signalFetchStarted: () => void = () => {};
  const fetchStarted = new Promise<void>((resolve) => {
    signalFetchStarted = resolve;
  });
  const heldUserB = new Promise<Schemas["UserOut"] | null>((resolve) => {
    releaseUserB = resolve;
  });
  mocks.fetchCurrentUser
    .mockImplementationOnce(() => {
      signalFetchStarted();
      return heldUserB;
    })
    .mockResolvedValueOnce(userA);

  const sessionPromise = resolvePatientSession(queryClient);
  await fetchStarted;
  mocks.setPointer({ id: PATIENT_A, role: "patient", locked: false });
  releaseUserB(userB);

  const session = await sessionPromise;
  expect.soft(mocks.readPointer()).toEqual({ id: PATIENT_A, role: "patient", locked: false });
  expect.soft(session).not.toMatchObject({ status: "online", user: { id: PATIENT_B } });
  expect.soft(mocks.messaging.confirmMessagingOwner).not.toHaveBeenCalled();
  expect.soft(mocks.messaging.lockMessagingAccess).toHaveBeenCalledOnce();
  expect.soft(meCacheAtOwnerCheck).not.toMatchObject({ id: PATIENT_B });
  expect.soft(queryClientMock.getQueryData(ME_KEY)).not.toMatchObject({ id: PATIENT_B });
  expect
    .soft(mocks.store.rememberOnlinePatientIfUnchanged)
    .toHaveBeenCalledWith({ id: PATIENT_B, role: "patient" }, {
      id: PATIENT_B,
      role: "patient",
      locked: false,
    } satisfies OwnerPointer);
});

it("finishes its auth check when the application query cache is cleared", async () => {
  mocks.messaging.confirmMessagingOwner.mockClear();
  mocks.messaging.lockMessagingAccess.mockClear();
  const userA = {
    id: PATIENT_A,
    role: "patient",
    full_name: "Synthetic patient A",
    email: "patient-a@example.invalid",
  } satisfies Schemas["UserOut"];
  mocks.setPointer(null);
  mocks.fetchCurrentUser.mockReset();

  let releaseUser: (user: Schemas["UserOut"]) => void = () => {};
  let signalFetchStarted: () => void = () => {};
  const fetchStarted = new Promise<void>((resolve) => {
    signalFetchStarted = resolve;
  });
  const heldUser = new Promise<Schemas["UserOut"]>((resolve) => {
    releaseUser = resolve;
  });
  mocks.fetchCurrentUser.mockImplementationOnce(() => {
    signalFetchStarted();
    return heldUser;
  });

  const queryClient = new QueryClient();
  const sessionPromise = resolvePatientSession(queryClient);
  await fetchStarted;
  await queryClient.cancelQueries();
  queryClient.clear();
  releaseUser(userA);

  await expect(sessionPromise).resolves.toEqual({ status: "online", user: userA });
  expect(mocks.messaging.confirmMessagingOwner).toHaveBeenCalledWith(PATIENT_A, "patient");
  expect(mocks.messaging.lockMessagingAccess).not.toHaveBeenCalled();
});
