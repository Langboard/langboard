import type { ICardCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import { buildDraftScopeKey, getCardDraftStore, persistCardDraft, type IDraftScope } from "./CardDraftStore";
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

export interface CardCommentDraft {
    content: string;
    anchor?: ICardCommentAnchor | null;
    request_uid?: string;
}

interface ICardCommentDraftStore {
    getSnapshot: (userUID: string, projectUID: string, cardUID: string) => CardCommentDraft;
    saveSnapshot: (userUID: string, projectUID: string, cardUID: string, draft: CardCommentDraft) => void;
    draftMap: Record<string, string | undefined>;
    snapshotMap: Record<string, CardCommentDraft | undefined>;
    getDraft: (userUID: string, projectUID: string, cardUID: string) => string;
    saveDraft: (userUID: string, projectUID: string, cardUID: string, content: string) => void;
    clearDraft: (userUID: string, projectUID: string, cardUID: string) => void;
}

const getCommentDraftStorageKey = (userUID: string, projectUID: string, cardUID: string) => `comment-${userUID}-${projectUID}-${cardUID}`;

const getCommentDraftScope = (userUID: string, projectUID: string, cardUID: string): IDraftScope => ({
    userUID,
    projectUID,
    kind: "comment-new",
    scopeUID: cardUID,
});

const clearLegacyDraft = (key: string) => {
    try {
        sessionStorage.removeItem(key);
    } catch {
        try {
            sessionStorage.setItem(key, JSON.stringify({ draft_version: 1, content: "" }));
        } catch {
            // A denied storage cannot be changed; the persistent scope remains authoritative.
        }
    }
};

const useCardCommentDraftStore = create(
    immer<ICardCommentDraftStore>((set, get) => ({
        draftMap: {},
        snapshotMap: {},
        getSnapshot: (userUID, projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            const scope = getCommentDraftScope(userUID, projectUID, cardUID);
            const drafts = getCardDraftStore();
            // Migration runs only before this scope has entered persistent storage.
            let hasStoredDraft = buildDraftScopeKey(scope) in drafts.cache;
            let hasPersistentRecord = false;
            try {
                hasPersistentRecord = localStorage.getItem(buildDraftScopeKey(scope)) !== null;
                hasStoredDraft ||= hasPersistentRecord;
            } catch {
                // In-memory drafts remain available when storage is inaccessible.
            }
            const persisted = drafts.getDraft(scope);
            if (hasStoredDraft) {
                if (hasPersistentRecord) clearLegacyDraft(key);
                if (!persisted) {
                    // Expired/malformed records also supersede legacy session copies.
                    persistCardDraft(scope, { content: "", savedAt: Date.now(), cleared: true });
                    return { content: "" };
                }
                return { content: persisted.content, anchor: persisted.anchor, request_uid: persisted.request_uid };
            }
            try {
                const stored = sessionStorage.getItem(key);
                if (stored === null) return { content: "" };
                let snapshot: CardCommentDraft = { content: stored };
                try {
                    const parsed = JSON.parse(stored);
                    if (parsed?.draft_version === 1) {
                        snapshot =
                            typeof parsed.content === "string"
                                ? { content: parsed.content, anchor: parsed.anchor, request_uid: parsed.request_uid }
                                : { content: "" };
                    }
                } catch {
                    // Legacy drafts contain plain editor content, including JSON text.
                }
                // Restoration runs in native render initializers. Persist without
                // notifying either Zustand store's React subscribers during render.
                const payload = { ...snapshot, savedAt: Date.now() };
                if (persistCardDraft(scope, payload)) clearLegacyDraft(key);
                return snapshot;
            } catch {
                return { content: "" };
            }
        },
        getDraft: (userUID, projectUID, cardUID) => get().getSnapshot(userUID, projectUID, cardUID).content,
        saveSnapshot: (userUID, projectUID, cardUID, draft) => {
            if (!draft.content && !draft.anchor && !draft.request_uid) {
                get().clearDraft(userUID, projectUID, cardUID);
                return;
            }
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = draft.content;
                state.snapshotMap[key] = draft;
            });
            getCardDraftStore().saveDraft(getCommentDraftScope(userUID, projectUID, cardUID), draft.content, {
                anchor: draft.anchor,
                request_uid: draft.request_uid,
            });
            // Do not remove the only durable copy if migration could not be persisted.
            try {
                const stored = localStorage.getItem(buildDraftScopeKey(getCommentDraftScope(userUID, projectUID, cardUID)));
                if (stored && stored === JSON.stringify(getCardDraftStore().getDraft(getCommentDraftScope(userUID, projectUID, cardUID)))) {
                    clearLegacyDraft(key);
                }
            } catch {
                // Storage failures must never block typing.
            }
        },
        saveDraft: (userUID, projectUID, cardUID, content) => {
            get().saveSnapshot(userUID, projectUID, cardUID, { ...get().getSnapshot(userUID, projectUID, cardUID), content });
        },
        clearDraft: (userUID, projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = "";
                state.snapshotMap[key] = { content: "" };
            });
            getCardDraftStore().clearDraft(getCommentDraftScope(userUID, projectUID, cardUID), true);
            clearLegacyDraft(key);
        },
    }))
);

export const getCardCommentDraftStore = () => useCardCommentDraftStore.getState();

export default useCardCommentDraftStore;
