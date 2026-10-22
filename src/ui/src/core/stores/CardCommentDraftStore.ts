import type { ICardCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
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

const useCardCommentDraftStore = create(
    immer<ICardCommentDraftStore>((set, get) => ({
        draftMap: {},
        snapshotMap: {},
        getSnapshot: (userUID, projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            if (key in get().snapshotMap) return get().snapshotMap[key] ?? { content: "" };
            if (key in get().draftMap) return { content: get().draftMap[key] ?? "" };
            try {
                const stored = sessionStorage.getItem(key) ?? "";
                try {
                    const parsed = JSON.parse(stored);
                    if (parsed?.draft_version === 1 && typeof parsed.content === "string")
                        return { content: parsed.content, anchor: parsed.anchor, request_uid: parsed.request_uid } as CardCommentDraft;
                } catch {
                    /* Legacy drafts contain plain editor content. */
                }
                return { content: stored };
            } catch {
                return { content: "" };
            }
        },
        getDraft: (userUID, projectUID, cardUID) => get().getSnapshot(userUID, projectUID, cardUID).content,
        saveSnapshot: (userUID, projectUID, cardUID, draft) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = draft.content;
                state.snapshotMap[key] = draft;
            });
            try {
                if (draft.content || draft.anchor) sessionStorage.setItem(key, JSON.stringify({ draft_version: 1, ...draft }));
                else sessionStorage.removeItem(key);
            } catch {
                /* Keep the account-scoped in-memory draft when storage is unavailable. */
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
            try {
                sessionStorage.removeItem(key);
            } catch {
                // The in-memory draft is already cleared.
            }
        },
    }))
);

export const getCardCommentDraftStore = () => useCardCommentDraftStore.getState();

export default useCardCommentDraftStore;
