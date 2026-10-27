import { fetchClient, getSessionGeneration } from "../index";
import type { Message, MessagingConversation, MessagingOwner } from "./api";
import { createIndexedDbMessagingPersistence } from "./indexeddb";
import { MessagingLocalError } from "./local-error";
import { errorParts, leaseOwner, pairKey, sendQueuedMessage } from "./local-utils";
import { createBrowserMessagingOwnerState, createMessagingId } from "./owner-state";

export { MessagingLocalError } from "./local-error";

export type MessagingSessionMarker =
  | { revision: string; locked: true }
  | {
      revision: string;
      locked: false;
      ownerId: string;
      role: MessagingOwner["role"];
    };

export type MessagingDraft = MessagingOwner &
  MessagingConversation & { text: string; updatedAt: string };

export type MessagingOutboxItem = MessagingOwner &
  MessagingConversation & {
    clientMessageId: string;
    kind: "text" | "voice";
    text?: string;
    blob?: Blob;
    fileName?: string;
    createdAt: string;
    status: "queued" | "failed";
    failureCode?: string;
  };

export interface MessagingPersistence {
  putDraft(record: MessagingDraft): Promise<void>;
  getDraft(
    ownerId: string,
    patientId: string,
    expectedDoctorId: string,
  ): Promise<MessagingDraft | null>;
  listDrafts(ownerId: string): Promise<MessagingDraft[]>;
  deleteDraft(ownerId: string, patientId: string, expectedDoctorId: string): Promise<void>;
  putOutbox(record: MessagingOutboxItem): Promise<void>;
  getOutbox(ownerId: string, clientMessageId: string): Promise<MessagingOutboxItem | null>;
  listOutbox(ownerId: string): Promise<MessagingOutboxItem[]>;
  deleteOutbox(ownerId: string, clientMessageId: string): Promise<void>;
  deleteOwner(ownerId: string): Promise<void>;
}

export interface MessagingOwnerState {
  read(): MessagingSessionMarker | null;
  write(marker: MessagingSessionMarker): void;
  subscribe(listener: (marker: MessagingSessionMarker | null) => void): () => void;
}

export type MessagingTransport = (item: MessagingOutboxItem) => Promise<Message>;
export type MessagingQueueOptions = {
  persistence?: MessagingPersistence;
  ownerState?: MessagingOwnerState;
  getSessionGeneration?: () => number;
  transport?: MessagingTransport;
  idFactory?: () => string;
  now?: () => string;
};

type Lease = MessagingOwner & { sessionGeneration: number; revision: string };

export function createMessagingLocalQueue(
  options: MessagingQueueOptions = {},
): MessagingLocalQueue {
  return new LocalMessagingQueue(options);
}

export interface MessagingLocalQueue {
  confirmOwner(owner: MessagingOwner, sessionGeneration: number): void;
  lock(): void;
  subscribe(listener: () => void): () => void;
  saveDraft(pair: MessagingConversation, text: string): Promise<MessagingDraft | null>;
  getDraft(pair: MessagingConversation): Promise<MessagingDraft | null>;
  deleteDraft(pair: MessagingConversation): Promise<void>;
  enqueueText(pair: MessagingConversation, text: string): Promise<MessagingOutboxItem>;
  enqueueVoice(
    pair: MessagingConversation,
    blob: Blob,
    fileName?: string,
  ): Promise<MessagingOutboxItem>;
  listOutbox(pair: MessagingConversation): Promise<MessagingOutboxItem[]>;
  counts(): Promise<{ drafts: number; unsent: number }>;
  retry(clientMessageId: string): Promise<MessagingOutboxItem>;
  deleteUnsent(clientMessageId: string): Promise<void>;
  flush(pair: MessagingConversation): Promise<Message[]>;
  clearOwnerAfterConfirmation(ownerId: string, confirmed: boolean): Promise<void>;
  dispose(): void;
}

class LocalMessagingQueue implements MessagingLocalQueue {
  private readonly persistence: MessagingPersistence;
  private readonly ownerState: MessagingOwnerState;
  private readonly getSessionGeneration: () => number;
  private readonly transport: MessagingTransport;
  private readonly idFactory: () => string;
  private readonly now: () => string;
  private readonly listeners = new Set<() => void>();
  private readonly flushes = new Map<string, Promise<Message[]>>();
  private lease: Lease | null = null;
  private readonly unsubscribeOwnerState: () => void;

  constructor(options: MessagingQueueOptions) {
    this.persistence = options.persistence ?? createIndexedDbMessagingPersistence();
    this.ownerState = options.ownerState ?? createBrowserMessagingOwnerState();
    this.getSessionGeneration =
      options.getSessionGeneration ?? (() => getSessionGeneration(fetchClient));
    this.transport = options.transport ?? sendQueuedMessage;
    this.idFactory = options.idFactory ?? createMessagingId;
    this.now = options.now ?? (() => new Date().toISOString());
    this.unsubscribeOwnerState = this.ownerState.subscribe((marker) => {
      if (!this.lease || marker?.revision === this.lease.revision) return;
      this.invalidateLocalAccess();
    });
  }

  confirmOwner(owner: MessagingOwner, sessionGeneration: number): void {
    if (sessionGeneration !== this.getSessionGeneration()) {
      throw new MessagingLocalError("messaging_session_changed");
    }
    const current = this.ownerState.read();
    const sameOwner =
      current?.locked === false && current.ownerId === owner.ownerId && current.role === owner.role;
    const marker: MessagingSessionMarker = sameOwner
      ? current
      : {
          revision: createMessagingId(),
          locked: false,
          ownerId: owner.ownerId,
          role: owner.role,
        };
    if (!sameOwner) this.ownerState.write(marker);
    this.lease = { ...owner, sessionGeneration, revision: marker.revision };
    this.notify();
  }

  lock(): void {
    this.invalidateLocalAccess();
    this.ownerState.write({ revision: createMessagingId(), locked: true });
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  async saveDraft(pair: MessagingConversation, text: string): Promise<MessagingDraft | null> {
    const lease = this.requireAccess(pair);
    if (text.length === 0) {
      await this.persistence.deleteDraft(lease.ownerId, pair.patientId, pair.expectedDoctorId);
      this.assertAccess(lease);
      return null;
    }
    const draft: MessagingDraft = { ...leaseOwner(lease), ...pair, text, updatedAt: this.now() };
    await this.persistence.putDraft(draft);
    this.assertAccess(lease);
    return draft;
  }

  async getDraft(pair: MessagingConversation): Promise<MessagingDraft | null> {
    const lease = this.requireAccess(pair);
    const draft = await this.persistence.getDraft(
      lease.ownerId,
      pair.patientId,
      pair.expectedDoctorId,
    );
    this.assertAccess(lease);
    return draft;
  }

  async deleteDraft(pair: MessagingConversation): Promise<void> {
    const lease = this.requireAccess(pair);
    await this.persistence.deleteDraft(lease.ownerId, pair.patientId, pair.expectedDoctorId);
    this.assertAccess(lease);
  }

  async enqueueText(pair: MessagingConversation, text: string): Promise<MessagingOutboxItem> {
    if (!text.trim()) throw new MessagingLocalError("messaging_empty_text");
    if (text.length > 20_000) throw new MessagingLocalError("messaging_text_too_long");
    return this.enqueue(pair, { kind: "text", text });
  }

  async enqueueVoice(
    pair: MessagingConversation,
    blob: Blob,
    fileName?: string,
  ): Promise<MessagingOutboxItem> {
    if (blob.size === 0) throw new MessagingLocalError("messaging_empty_voice");
    if (blob.size > 25 * 1024 * 1024) throw new MessagingLocalError("messaging_voice_too_large");
    return this.enqueue(pair, { kind: "voice", blob, fileName });
  }

  async listOutbox(pair: MessagingConversation): Promise<MessagingOutboxItem[]> {
    const lease = this.requireAccess(pair);
    const items = await this.persistence.listOutbox(lease.ownerId);
    this.assertAccess(lease);
    return items
      .filter(
        (item) =>
          item.patientId === pair.patientId && item.expectedDoctorId === pair.expectedDoctorId,
      )
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  }

  async counts(): Promise<{ drafts: number; unsent: number }> {
    const lease = this.requireAccess();
    const [drafts, outbox] = await Promise.all([
      this.persistence.listDrafts(lease.ownerId),
      this.persistence.listOutbox(lease.ownerId),
    ]);
    this.assertAccess(lease);
    return {
      drafts: drafts.filter((draft) => draft.text.length > 0).length,
      unsent: outbox.length,
    };
  }

  async retry(clientMessageId: string): Promise<MessagingOutboxItem> {
    const lease = this.requireAccess();
    const item = await this.persistence.getOutbox(lease.ownerId, clientMessageId);
    this.assertAccess(lease);
    if (!item) throw new MessagingLocalError("messaging_outbox_missing");
    const retried: MessagingOutboxItem = { ...item, status: "queued" };
    delete retried.failureCode;
    await this.persistence.putOutbox(retried);
    this.assertAccess(lease);
    return retried;
  }

  async deleteUnsent(clientMessageId: string): Promise<void> {
    const lease = this.requireAccess();
    const item = await this.persistence.getOutbox(lease.ownerId, clientMessageId);
    this.assertAccess(lease);
    if (!item) return;
    await this.persistence.deleteOutbox(lease.ownerId, clientMessageId);
    this.assertAccess(lease);
  }

  flush(pair: MessagingConversation): Promise<Message[]> {
    const lease = this.requireAccess(pair);
    const key = pairKey(lease.ownerId, pair);
    const previous = this.flushes.get(key) ?? Promise.resolve([]);
    const current = previous.catch(() => []).then(() => this.flushPair(lease, pair));
    this.flushes.set(key, current);
    return current.finally(() => {
      if (this.flushes.get(key) === current) this.flushes.delete(key);
    });
  }

  async clearOwnerAfterConfirmation(ownerId: string, confirmed: boolean): Promise<void> {
    if (!confirmed) throw new MessagingLocalError("messaging_clear_not_confirmed");
    if (this.lease?.ownerId === ownerId) this.lock();
    await this.persistence.deleteOwner(ownerId);
  }

  dispose(): void {
    this.unsubscribeOwnerState();
    this.listeners.clear();
    this.lease = null;
  }

  private async enqueue(
    pair: MessagingConversation,
    payload: Pick<MessagingOutboxItem, "kind" | "text" | "blob" | "fileName">,
  ): Promise<MessagingOutboxItem> {
    const lease = this.requireAccess(pair);
    const item: MessagingOutboxItem = {
      ...leaseOwner(lease),
      ...pair,
      ...payload,
      clientMessageId: this.idFactory(),
      createdAt: this.now(),
      status: "queued",
    };
    await this.persistence.putOutbox(item);
    this.assertAccess(lease);
    return item;
  }

  private async flushPair(lease: Lease, pair: MessagingConversation): Promise<Message[]> {
    this.assertAccess(lease, pair);
    const pending = (await this.persistence.listOutbox(lease.ownerId))
      .filter(
        (item) =>
          item.status === "queued" &&
          item.patientId === pair.patientId &&
          item.expectedDoctorId === pair.expectedDoctorId,
      )
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt));
    this.assertAccess(lease, pair);
    const accepted: Message[] = [];
    for (const item of pending) {
      this.assertAccess(lease, pair);
      try {
        const message = await this.transport(item);
        this.assertAccess(lease, pair);
        if (message.client_message_id !== item.clientMessageId) {
          throw new MessagingLocalError("messaging_idempotency_mismatch");
        }
        await this.persistence.deleteOutbox(lease.ownerId, item.clientMessageId);
        this.assertAccess(lease, pair);
        accepted.push(message);
      } catch (error) {
        if (error instanceof MessagingLocalError && error.code === "messaging_locked") throw error;
        this.assertAccess(lease, pair);
        const { status, code } = errorParts(error);
        if (status === 401 || status === 4401) {
          this.lock();
          throw new MessagingLocalError("messaging_locked");
        }
        if (status === undefined || status === 408 || status === 429 || status >= 500) break;
        const failed: MessagingOutboxItem = {
          ...item,
          status: "failed",
          failureCode: code ?? `http_${status}`,
        };
        await this.persistence.putOutbox(failed);
        this.assertAccess(lease, pair);
        break;
      }
    }
    return accepted;
  }

  private requireAccess(pair?: MessagingConversation): Lease {
    const lease = this.lease;
    if (!lease) throw new MessagingLocalError("messaging_locked");
    this.assertAccess(lease, pair);
    return lease;
  }

  private assertAccess(lease: Lease, pair?: MessagingConversation): void {
    if (this.lease !== lease || this.getSessionGeneration() !== lease.sessionGeneration) {
      this.invalidateLocalAccess();
      throw new MessagingLocalError("messaging_locked");
    }
    let marker: MessagingSessionMarker | null;
    try {
      marker = this.ownerState.read();
    } catch {
      this.invalidateLocalAccess();
      throw new MessagingLocalError("messaging_locked");
    }
    if (
      marker?.locked !== false ||
      marker.revision !== lease.revision ||
      marker.ownerId !== lease.ownerId ||
      marker.role !== lease.role
    ) {
      this.invalidateLocalAccess();
      throw new MessagingLocalError("messaging_locked");
    }
    if (
      pair &&
      (lease.role === "patient"
        ? pair.patientId !== lease.ownerId
        : pair.expectedDoctorId !== lease.ownerId)
    ) {
      throw new MessagingLocalError("messaging_owner_mismatch");
    }
  }

  private invalidateLocalAccess(): void {
    if (!this.lease) return;
    this.lease = null;
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
