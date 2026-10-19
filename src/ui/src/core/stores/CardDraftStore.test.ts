import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

class MemoryLocalStorage {
    private map = new Map<string, string>();
    get length() {
        return this.map.size;
    }
    key(index: number) {
        return [...this.map.keys()][index] ?? null;
    }
    getItem(key: string) {
        return this.map.get(key) ?? null;
    }
    setItem(key: string, value: string) {
        this.map.set(key, value);
    }
    removeItem(key: string) {
        this.map.delete(key);
    }
    clear() {
        this.map.clear();
    }
}

const storage = new MemoryLocalStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });

const { default: useCardDraftStore, DRAFT_TTL_MS, getCardDraftStore } = await import("./CardDraftStore.ts");
type IDraftScope = import("./CardDraftStore.ts").IDraftScope;

const userA = "user-a";
const userB = "user-b";
const project1 = "project-1";
const project2 = "project-2";
const card1 = "card-1";
const comment1 = "comment-1";

const scope = (overrides: Partial<IDraftScope> = {}): IDraftScope => ({
    userUID: userA,
    projectUID: project1,
    kind: "comment-new",
    scopeUID: card1,
    ...overrides,
});

describe("CardDraftStore", () => {
    beforeEach(() => {
        storage.clear();
        useCardDraftStore.setState({ cache: {} });
    });

    it("saves and restores a draft across store recreation (localStorage)", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "작성 중인 댓글");
        useCardDraftStore.setState({ cache: {} });
        const restored = getCardDraftStore().getDraft(scope());
        assert.equal(restored?.content, "작성 중인 댓글");
        assert.ok(restored && restored.savedAt > 0);
    });

    it("isolates drafts by user, project, kind and scope", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "base");
        store.saveDraft(scope({ userUID: userB }), "other user");
        store.saveDraft(scope({ projectUID: project2 }), "other project");
        store.saveDraft(scope({ kind: "comment-edit", scopeUID: comment1 }), "edit draft");
        store.saveDraft(scope({ kind: "card-new", scopeUID: "new" }), "new card draft");

        assert.equal(getCardDraftStore().getDraft(scope())?.content, "base");
        assert.equal(getCardDraftStore().getDraft(scope({ userUID: userB }))?.content, "other user");
        assert.equal(getCardDraftStore().getDraft(scope({ projectUID: project2 }))?.content, "other project");
        assert.equal(getCardDraftStore().getDraft(scope({ kind: "comment-edit", scopeUID: comment1 }))?.content, "edit draft");
        assert.equal(getCardDraftStore().getDraft(scope({ kind: "card-new", scopeUID: "new" }))?.content, "new card draft");
    });

    it("clears a draft after successful submit", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "to clear");
        store.clearDraft(scope());
        assert.equal(getCardDraftStore().getDraft(scope()), null);
        assert.equal(storage.getItem("lb-draft-v1:user-a:project-1:comment-new:card-1"), null);
    });

    it("keeps the draft on save failure (clear only on success)", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "keep me");
        // Failure path simply does not call clearDraft; the draft must survive.
        assert.equal(getCardDraftStore().getDraft(scope())?.content, "keep me");
    });

    it("discards drafts older than the TTL", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "stale");
        const key = "lb-draft-v1:user-a:project-1:comment-new:card-1";
        const stalePayload = JSON.stringify({ content: "stale", savedAt: Date.now() - DRAFT_TTL_MS - 1 });
        storage.setItem(key, stalePayload);
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(scope()), null);
        assert.equal(storage.getItem(key), null, "expired draft is removed on read");
    });

    it("empty content removes the draft instead of storing an empty shell", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "temporary");
        store.saveDraft(scope(), "");
        assert.equal(getCardDraftStore().getDraft(scope()), null);
    });

    it("leaves an in-memory tombstone when persisted removal fails, so a cleared draft cannot resurrect", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "submitted draft");
        const key = "lb-draft-v1:user-a:project-1:comment-new:card-1";
        const originalRemove = storage.removeItem.bind(storage);
        storage.removeItem = () => {
            throw new DOMException("Denied", "SecurityError");
        };
        try {
            store.clearDraft(scope());
            // The persisted value is still there, but reads must observe the cleared tombstone.
            assert.ok(storage.getItem(key));
            assert.equal(getCardDraftStore().getDraft(scope())?.content, "");
        } finally {
            storage.removeItem = originalRemove;
        }
        // Once removal succeeds the draft is gone for good.
        store.clearDraft(scope());
        assert.equal(getCardDraftStore().getDraft(scope()), null);
    });

    it("keeps typing unblocked and account-scoped when storage is fully unavailable", () => {
        const store = getCardDraftStore();
        const originalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage")!;
        Object.defineProperty(globalThis, "localStorage", {
            configurable: true,
            value: {
                getItem: () => {
                    throw new DOMException("Denied", "SecurityError");
                },
                setItem: () => {
                    throw new DOMException("Denied", "SecurityError");
                },
                removeItem: () => {
                    throw new DOMException("Denied", "SecurityError");
                },
            },
        });
        try {
            store.saveDraft(scope(), "offline draft");
            assert.equal(getCardDraftStore().getDraft(scope())?.content, "offline draft");
            store.clearDraft(scope());
            assert.equal(getCardDraftStore().getDraft(scope())?.content, "");
            // A different account never sees this scope's draft.
            assert.equal(getCardDraftStore().getDraft(scope({ userUID: userB })), null);
        } finally {
            Object.defineProperty(globalThis, "localStorage", originalStorage);
        }
    });
});
