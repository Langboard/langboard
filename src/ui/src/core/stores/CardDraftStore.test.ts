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

const draftWindow = new EventTarget();
Object.defineProperty(globalThis, "window", { value: draftWindow, configurable: true });
const storage = new MemoryLocalStorage();
const session = new MemoryLocalStorage();
Object.defineProperty(globalThis, "sessionStorage", { value: session, configurable: true });
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });

const { default: useCardDraftStore, DRAFT_TTL_MS, getCardDraftStore } = await import("./CardDraftStore.ts");
const { default: useCommentDraftStore, getCardCommentDraftStore } = await import("./CardCommentDraftStore.ts");
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

    it("persists exact new-card title/body metadata without interpreting legacy titles", () => {
        const cardScope = scope({ kind: "card-new", scopeUID: "column-a" });
        const content = JSON.stringify({ description: "this is a literal title" });
        getCardDraftStore().saveDraft(cardScope, content);
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(cardScope)?.content, content);
        assert.equal(getCardDraftStore().getDraft(cardScope)?.description, undefined);
        const body = "  ## Body\n\n한글\n";
        getCardDraftStore().saveDraft(cardScope, content, { description: body, request_uid: "session-revision" });
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(cardScope)?.description, body);
        assert.equal(getCardDraftStore().getDraft(cardScope)?.request_uid, "session-revision");
        getCardDraftStore().saveDraft(cardScope, "", { description: body });
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(cardScope)?.description, body);
        getCardDraftStore().saveDraft(cardScope, "", { description: "" });
        assert.equal(getCardDraftStore().getDraft(cardScope), null);
    });

    it("restores an intentional empty replacement of an existing description", () => {
        const editScope = scope({ kind: "card-edit", scopeUID: card1 });
        getCardDraftStore().saveDraft(editScope, "");
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(editScope)?.content, "");
        getCardDraftStore().clearDraft(editScope);
        assert.equal(getCardDraftStore().getDraft(editScope), null);
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

    it("preserves an intentional empty comment edit and its saved baseline across account changes and recreation", () => {
        const editScope = scope({ kind: "comment-edit", scopeUID: comment1 });
        const otherScope = { ...editScope, userUID: userB };
        const baseline = { base_content: "Saved original", base_revision: "revision-1", base_updated_at: new Date(0).toISOString() };
        getCardDraftStore().saveDraft(editScope, "", baseline);
        getCardDraftStore().saveDraft(otherScope, "Other account work", baseline);
        useCardDraftStore.setState({ cache: {} });
        assert.equal(getCardDraftStore().getDraft(editScope)?.content, "");
        assert.equal(getCardDraftStore().getDraft(editScope)?.base_revision, "revision-1");
        assert.equal(getCardDraftStore().getDraft(otherScope)?.content, "Other account work");
        getCardDraftStore().clearDraft(editScope);
        assert.equal(getCardDraftStore().getDraft(editScope), null);
        assert.equal(getCardDraftStore().getDraft(otherScope)?.content, "Other account work");
    });

    it("clears a draft after successful submit", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "to clear");
        store.clearDraft(scope());
        assert.equal(getCardDraftStore().getDraft(scope()), null);
        assert.equal(storage.getItem("lb-draft-v1:user-a:project-1:comment-new:card-1"), null);
    });

    it("does not restore a cleared description as an empty replacement when removal fails", () => {
        const editScope = scope({ kind: "card-edit", scopeUID: card1 });
        getCardDraftStore().saveDraft(editScope, "unsaved description");
        const originalRemove = storage.removeItem.bind(storage);
        storage.removeItem = () => {
            throw new DOMException("Denied", "SecurityError");
        };
        try {
            getCardDraftStore().clearDraft(editScope);
            assert.equal(getCardDraftStore().getDraft(editScope), null);
            useCardDraftStore.setState({ cache: {} });
            assert.equal(getCardDraftStore().getDraft(editScope), null, "reload must keep the saved description instead of a blank draft");
        } finally {
            storage.removeItem = originalRemove;
        }
    });

    it("keeps the draft on save failure (clear only on success)", () => {
        const store = getCardDraftStore();
        store.saveDraft(scope(), "keep me");
        // Failure path simply does not call clearDraft; the draft must survive.
        assert.equal(getCardDraftStore().getDraft(scope())?.content, "keep me");
    });

    it("preserves failed-write drafts when another tab clears storage, while committed drafts follow that clear", () => {
        const pendingScope = scope();
        const committedScope = scope({ userUID: userB });
        const store = getCardDraftStore();
        store.saveDraft(pendingScope, "Older durable content");
        store.saveDraft(committedScope, "Other account durable content");
        const originalSet = storage.setItem;
        storage.setItem = () => {
            throw new DOMException("Quota exceeded", "QuotaExceededError");
        };
        try {
            store.saveDraft(pendingScope, "Newest unsaved content");
            assert.equal(store.getDraft(pendingScope)?.content, "Newest unsaved content");
            storage.clear();
            draftWindow.dispatchEvent(Object.assign(new Event("storage"), { key: null, storageArea: storage }));
            assert.equal(store.getDraft(pendingScope)?.content, "Newest unsaved content", "the only remaining copy must survive another tab's clear");
            assert.equal(store.getDraft(committedScope), null, "committed drafts still follow external clears");
            draftWindow.dispatchEvent(
                Object.assign(new Event("storage"), { key: "lb-draft-v1:user-a:project-1:comment-new:card-1", storageArea: storage })
            );
            assert.equal(
                store.getDraft(pendingScope)?.content,
                "Newest unsaved content",
                "targeted external events must also preserve failed writes"
            );
        } finally {
            storage.setItem = originalSet;
        }
        store.saveDraft(pendingScope, "Persistence recovered");
        storage.clear();
        draftWindow.dispatchEvent(Object.assign(new Event("storage"), { key: null, storageArea: storage }));
        assert.equal(store.getDraft(pendingScope), null, "successful persistence restores ordinary external-clear behavior");
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
            // Storage retains a cleared marker, which must never become an editable draft.
            assert.ok(storage.getItem(key));
            assert.equal(getCardDraftStore().getDraft(scope()), null);
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
            assert.equal(getCardDraftStore().getDraft(scope()), null);
            // A different account never sees this scope's draft.
            assert.equal(getCardDraftStore().getDraft(scope({ userUID: userB })), null);
        } finally {
            Object.defineProperty(globalThis, "localStorage", originalStorage);
        }
    });
});

describe("CardCommentDraftStore persistence", () => {
    const key = "lb-draft-v1:user-a:project-1:comment-new:card-1";
    const legacyKey = "comment-user-a-project-1-card-1";
    const image = {
        type: "ImageRegionSelector" as const,
        version: 1 as const,
        source: { type: "card_attachment" as const, attachment_uid: "attachment", revision: "revision" },
        shape: "point" as const,
        image_width: 1200,
        image_height: 800,
        x: 40,
        y: 60,
        width: 0,
        height: 0,
    };
    const snapshot = { content: "Retained image draft", anchor: image, request_uid: "retry-uuid" };
    const store = () => getCardCommentDraftStore();
    const read = () => store().getSnapshot(userA, project1, card1);
    const recreate = () => {
        useCardDraftStore.setState({ cache: {} });
        useCommentDraftStore.setState({ draftMap: {}, snapshotMap: {} });
    };
    beforeEach(() => {
        storage.clear();
        session.clear();
        recreate();
    });

    it("restores image metadata and failed-submit retry identity after session/cache recreation", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        session.clear();
        recreate();
        assert.deepEqual(read(), snapshot);
        store().saveDraft(userA, project1, card1, "Retried content");
        recreate();
        assert.deepEqual(read(), { ...snapshot, content: "Retried content" });
    });

    it("persists anchor-only and text-anchor snapshots exactly", () => {
        store().saveSnapshot(userA, project1, card1, { ...snapshot, content: "" });
        recreate();
        assert.deepEqual(read(), { ...snapshot, content: "" });
        const text = {
            version: 1 as const,
            exact: "quote",
            prefix: "before",
            suffix: "after",
            start_block: "a",
            end_block: "b",
            start_path: [0],
            end_path: [1],
        };
        store().saveSnapshot(userA, project1, card1, { content: "text", anchor: text });
        recreate();
        assert.deepEqual(read().anchor, text);
    });

    it("migrates plain legacy strings, JSON editor text and version 1 metadata", () => {
        for (const legacy of ["legacy text", JSON.stringify({ content: "literal JSON" }), JSON.stringify({ draft_version: 1, ...snapshot })]) {
            storage.clear();
            recreate();
            session.setItem(legacyKey, legacy);
            const expected = legacy.includes("draft_version") ? snapshot : { content: legacy };
            assert.equal(read().content, expected.content);
            assert.equal(session.getItem(legacyKey), null);
            recreate();
            assert.equal(read().content, expected.content);
            if (expected === snapshot) assert.deepEqual(read(), snapshot);
        }
    });

    it("prefers persistent metadata over session drafts and isolates actor, project and card", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        session.setItem(legacyKey, "older draft");
        recreate();
        assert.deepEqual(read(), snapshot);
        for (const args of [
            [userB, project1, card1],
            [userA, project2, card1],
            [userA, project1, "other-card"],
        ]) {
            assert.equal(store().getSnapshot(args[0], args[1], args[2]).content, "");
        }
    });

    it("discards expired cached metadata and stale migration content", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        const payload = JSON.parse(storage.getItem(key)!);
        payload.savedAt = Date.now() - DRAFT_TTL_MS - 1;
        storage.setItem(key, JSON.stringify(payload));
        recreate();
        session.setItem(legacyKey, "stale legacy");
        assert.equal(read().content, "");
        recreate();
        assert.equal(read().content, "");
        useCardDraftStore.setState({ cache: { [key]: payload } });
        assert.equal(read().anchor, undefined);
    });

    it("clears both storage generations and persists tombstones when removal fails", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        session.setItem(legacyKey, JSON.stringify({ draft_version: 1, ...snapshot }));
        const removeLocal = storage.removeItem;
        const removeSession = session.removeItem;
        storage.removeItem = session.removeItem = () => {
            throw new Error("denied");
        };
        try {
            store().clearDraft(userA, project1, card1);
            recreate();
            assert.equal(read().content, "");
            assert.equal(read().anchor, undefined);
        } finally {
            storage.removeItem = removeLocal;
            session.removeItem = removeSession;
        }
        store().clearDraft(userA, project1, card1);
        recreate();
        assert.equal(read().content, "");
    });

    it("reads cleared snapshots without rewriting storage or refreshing their timestamp", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        store().clearDraft(userA, project1, card1);
        const marker = storage.getItem(key);
        const write = storage.setItem;
        let writes = 0;
        storage.setItem = function (name, value) {
            writes += 1;
            write.call(this, name, value);
        };
        try {
            for (let index = 0; index < 5; index += 1) {
                recreate();
                session.setItem(legacyKey, "stale session copy");
                assert.equal(read().content, "");
                assert.equal(session.getItem(legacyKey), null);
            }
            assert.equal(writes, 0, "stable cleared reads must not generate cross-tab storage events");
            assert.equal(storage.getItem(key), marker);
        } finally {
            storage.setItem = write;
        }
    });

    it("persists an in-memory cleared marker when failed storage recovers", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        const write = storage.setItem;
        storage.setItem = () => {
            throw new Error("quota");
        };
        try {
            store().clearDraft(userA, project1, card1);
            assert.equal(read().content, "");
            assert.equal(JSON.parse(storage.getItem(key)!).content, snapshot.content);
        } finally {
            storage.setItem = write;
        }
        assert.equal(read().content, "");
        assert.equal(JSON.parse(storage.getItem(key)!).cleared, true);
        recreate();
        assert.equal(read().content, "");
    });

    it("empty snapshots remove legacy backups even after a failed migration write", () => {
        session.setItem(legacyKey, "backup");
        const setItem = storage.setItem;
        storage.setItem = () => {
            throw new Error("quota");
        };
        try {
            assert.equal(read().content, "backup");
            store().saveSnapshot(userA, project1, card1, { content: "" });
            recreate();
            assert.equal(read().content, "");
        } finally {
            storage.setItem = setItem;
        }
    });

    it("never remigrates a cleared session copy when session cleanup is fully denied", () => {
        store().saveSnapshot(userA, project1, card1, snapshot);
        session.setItem(legacyKey, JSON.stringify({ draft_version: 1, ...snapshot }));
        const removeSession = session.removeItem;
        const writeSession = session.setItem;
        session.removeItem = session.setItem = () => {
            throw new Error("session denied");
        };
        try {
            store().clearDraft(userA, project1, card1);
            assert.equal(read().content, "");
            recreate();
            assert.equal(read().content, "");
            const marker = JSON.parse(storage.getItem(key)!);
            marker.savedAt = Date.now() - DRAFT_TTL_MS - 1;
            storage.setItem(key, JSON.stringify(marker));
            recreate();
            assert.equal(read().content, "");
            assert.equal(session.getItem(legacyKey), JSON.stringify({ draft_version: 1, ...snapshot }));
            storage.setItem(key, JSON.stringify({ ...snapshot, savedAt: Date.now() - DRAFT_TTL_MS - 1 }));
            recreate();
            assert.equal(read().content, "");
            recreate();
            assert.equal(read().content, "", "expired snapshots cannot remigrate a denied session copy");
        } finally {
            session.removeItem = removeSession;
            session.setItem = writeSession;
        }
    });

    it("does not notify render subscribers during synchronous legacy migration", () => {
        session.setItem(legacyKey, JSON.stringify({ draft_version: 1, ...snapshot }));
        let notifications = 0;
        const unsubscribeComment = useCommentDraftStore.subscribe(() => {
            notifications += 1;
        });
        const unsubscribeDraft = useCardDraftStore.subscribe(() => {
            notifications += 1;
        });
        try {
            assert.deepEqual(read(), snapshot);
            assert.equal(notifications, 0);
            recreate();
            assert.deepEqual(read(), snapshot);
        } finally {
            unsubscribeComment();
            unsubscribeDraft();
        }
    });

    it("keeps typing and retry metadata in memory when persistence fails", () => {
        session.setItem(legacyKey, "legacy backup");
        const setItem = storage.setItem;
        storage.setItem = () => {
            throw new Error("quota");
        };
        try {
            assert.equal(read().content, "legacy backup");
            assert.equal(session.getItem(legacyKey), "legacy backup");
            store().saveSnapshot(userA, project1, card1, snapshot);
            assert.deepEqual(read(), snapshot);
            assert.equal(session.getItem(legacyKey), "legacy backup");
            store().clearDraft(userA, project1, card1);
            assert.equal(read().content, "");
        } finally {
            storage.setItem = setItem;
        }
    });

    it("handles malformed payloads and legacy versioned data without throwing", () => {
        for (const malformed of [
            "null",
            "[]",
            "{",
            JSON.stringify({ content: "bad", savedAt: "yesterday" }),
            JSON.stringify({ content: "bad", savedAt: "yesterday", cleared: true }),
        ]) {
            storage.setItem(key, malformed);
            recreate();
            assert.doesNotThrow(read);
            assert.equal(read().content, "");
            const marker = JSON.parse(storage.getItem(key)!);
            assert.equal(marker.cleared, true);
            assert.equal(typeof marker.savedAt, "number");
        }
        session.setItem(legacyKey, JSON.stringify({ draft_version: 1, content: 42 }));
        recreate();
        assert.equal(read().content, "");
    });
});
