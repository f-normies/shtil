import { describe, expect, it } from "vitest";
import type { components } from "../schema";
import type {
  MessagingDraft,
  MessagingOutboxItem,
  MessagingOwnerState,
  MessagingPersistence,
  MessagingQueueOptions,
  MessagingSessionMarker,
} from "./local";
import * as local from "./local";

type Schemas = components["schemas"];

const patient = { ownerId: "patient-1", role: "patient" as const };
const pair = { patientId: "patient-1", expectedDoctorId: "doctor-1" };

function message(clientMessageId: string): Schemas["MessageOut"] {
  return {
    analysis_skip_reason: null,
    analysis_status: null,
    client_message_id: clientMessageId,
    created_at: "2026-10-25T10:00:00Z",
    duration_ms: null,
    id: `message-${clientMessageId}`,
    kind: "text",
    sender_id: patient.ownerId,
    seq: 1,
    text: "Привет",
    transcription_error_code: null,
    transcription_status: null,
    transcription_text: null,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

type SetupOptions = Omit<Partial<MessagingQueueOptions>, "persistence"> & {
  persistence?: Partial<MessagingPersistence>;
};

function setup(options: SetupOptions = {}) {
  expect(local.createMessagingLocalQueue).toBeTypeOf("function");
  if (typeof local.createMessagingLocalQueue !== "function") return undefined;

  const drafts = new Map<string, MessagingDraft>();
  const outbox = new Map<string, MessagingOutboxItem>();
  const basePersistence: MessagingPersistence = {
    async putDraft(record) {
      drafts.set(`${record.ownerId}/${record.patientId}/${record.expectedDoctorId}`, record);
    },
    async getDraft(ownerId, patientId, doctorId) {
      return drafts.get(`${ownerId}/${patientId}/${doctorId}`) ?? null;
    },
    async listDrafts(ownerId: string) {
      return [...drafts.values()].filter((item) => item.ownerId === ownerId);
    },
    async deleteDraft(ownerId: string, patientId: string, doctorId: string) {
      drafts.delete(`${ownerId}/${patientId}/${doctorId}`);
    },
    async putOutbox(record) {
      outbox.set(`${record.ownerId}/${record.clientMessageId}`, record);
    },
    async getOutbox(ownerId: string, clientMessageId: string) {
      return outbox.get(`${ownerId}/${clientMessageId}`) ?? null;
    },
    async listOutbox(ownerId: string) {
      return [...outbox.values()].filter((item) => item.ownerId === ownerId);
    },
    async deleteOutbox(ownerId: string, clientMessageId: string) {
      outbox.delete(`${ownerId}/${clientMessageId}`);
    },
    async deleteOwner(ownerId: string) {
      for (const [key, item] of drafts) if (item.ownerId === ownerId) drafts.delete(key);
      for (const [key, item] of outbox) if (item.ownerId === ownerId) outbox.delete(key);
    },
  };
  let shared: MessagingSessionMarker | null = null;
  const subscribers = new Set<(record: MessagingSessionMarker | null) => void>();
  const ownerState: MessagingOwnerState = options.ownerState ?? {
    read: () => shared,
    write: (record) => {
      shared = record;
      for (const notify of subscribers) notify(shared);
    },
    subscribe: (notify) => {
      subscribers.add(notify);
      return () => {
        subscribers.delete(notify);
      };
    },
  };
  let generation = 1;
  const persistence: MessagingPersistence = { ...basePersistence, ...options.persistence };
  const queue = local.createMessagingLocalQueue({
    ...options,
    persistence,
    ownerState,
    getSessionGeneration: () => generation,
    idFactory: () => "client-fixed",
    now: () => "2026-10-25T10:00:00Z",
  });
  return {
    queue,
    persistence: basePersistence,
    outbox,
    ownerState,
    subscribers,
    setGeneration: (value: number) => {
      generation = value;
    },
  };
}

describe("durable messaging queue", () => {
  it("keeps input intact when a lock arrives during the durable enqueue", async () => {
    const started = deferred<void>();
    const release = deferred<void>();
    const state = setup({
      persistence: {
        async putOutbox(record: MessagingOutboxItem) {
          started.resolve();
          await release.promise;
          await stateBase.putOutbox(record);
        },
      },
    });
    if (!state) return;
    const stateBase = state.persistence;
    await state.queue.confirmOwner(patient, 1);
    let input = "Сообщение";
    const enqueue = state.queue.enqueueText(pair, input).then(() => {
      input = "";
    });
    await started.promise;
    state.queue.lock();
    release.resolve();

    await expect(enqueue).rejects.toMatchObject({ code: "messaging_locked" });
    expect(input).toBe("Сообщение");
  });

  it("refuses another patient's pair under a confirmed patient owner", async () => {
    const state = setup();
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);

    await expect(
      state.queue.getDraft({ patientId: "patient-2", expectedDoctorId: "doctor-1" }),
    ).rejects.toMatchObject({ code: "messaging_owner_mismatch" });
  });

  it("saves a draft durably and counts only the confirmed owner", async () => {
    const state = setup();
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);
    await state.queue.saveDraft(pair, "Черновик");

    await expect(state.queue.getDraft(pair)).resolves.toMatchObject({ text: "Черновик" });
    await expect(state.queue.counts()).resolves.toEqual({ drafts: 1, unsent: 0 });
  });

  it("keeps the same owner active in two tabs with local session generations", async () => {
    const first = setup();
    if (!first) return;
    const second = setup({ ownerState: first.ownerState });
    if (!second) return;
    first.setGeneration(11);
    second.setGeneration(22);

    first.queue.confirmOwner(patient, 11);
    second.queue.confirmOwner(patient, 22);

    await expect(first.queue.counts()).resolves.toEqual({ drafts: 0, unsent: 0 });
    await expect(second.queue.counts()).resolves.toEqual({ drafts: 0, unsent: 0 });
  });

  it("keeps queued data after a transport response arrives after lock", async () => {
    const started = deferred<void>();
    const respond = deferred<Schemas["MessageOut"]>();
    const state = setup({
      transport: async () => {
        started.resolve();
        return respond.promise;
      },
    });
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);
    const pending = await state.queue.enqueueText(pair, "Сообщение");
    const flushing = state.queue.flush(pair);
    await started.promise;
    state.queue.lock();
    respond.resolve(message(String(pending.clientMessageId)));

    await expect(flushing).rejects.toMatchObject({ code: "messaging_locked" });
    expect(state.outbox.has(`${patient.ownerId}/${pending.clientMessageId}`)).toBe(true);
  });

  it("retries a permanent failure with the same id and payload", async () => {
    const calls: Array<{ id: string; text: string }> = [];
    const state = setup({
      transport: async (record: MessagingOutboxItem) => {
        calls.push({ id: record.clientMessageId, text: record.text ?? "" });
        if (calls.length === 1) throw { status: 422, code: "invalid_message" };
        return message(record.clientMessageId);
      },
    });
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);
    const queued = await state.queue.enqueueText(pair, "Повтори это сообщение");
    await state.queue.flush(pair);
    await state.queue.retry(queued.clientMessageId);
    await state.queue.flush(pair);

    expect(calls).toEqual([
      { id: "client-fixed", text: "Повтори это сообщение" },
      { id: "client-fixed", text: "Повтори это сообщение" },
    ]);
    expect(state.outbox.has(`${patient.ownerId}/${queued.clientMessageId}`)).toBe(false);
  });

  it("leaves a network failure queued for reconnect", async () => {
    const state = setup({
      transport: async () => {
        throw new TypeError("offline");
      },
    });
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);
    const queued = await state.queue.enqueueText(pair, "Останется в очереди");

    await state.queue.flush(pair);

    await expect(state.queue.listOutbox(pair)).resolves.toMatchObject([
      { clientMessageId: queued.clientMessageId, text: "Останется в очереди", status: "queued" },
    ]);
  });

  it("does not retarget a conversation_changed item to another doctor", async () => {
    const calls: string[] = [];
    const state = setup({
      transport: async (record: MessagingOutboxItem) => {
        calls.push(record.expectedDoctorId);
        throw { status: 409, code: "conversation_changed" };
      },
    });
    if (!state) return;
    await state.queue.confirmOwner(patient, 1);
    const queued = await state.queue.enqueueText(pair, "Только прежнему врачу");
    await state.queue.flush(pair);
    await state.queue.retry(queued.clientMessageId);
    await state.queue.flush({ ...pair, expectedDoctorId: "doctor-2" });

    expect(calls).toEqual(["doctor-1"]);
    await expect(state.queue.listOutbox(pair)).resolves.toMatchObject([
      { clientMessageId: queued.clientMessageId, expectedDoctorId: "doctor-1", status: "queued" },
    ]);
  });
});
