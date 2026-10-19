import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface ICardCommentDraftStore {
    draftMap: Record<string, string | undefined>;
    getDraft: (userUID: string, projectUID: string, cardUID: string) => string;
    saveDraft: (userUID: string, projectUID: string, cardUID: string, content: string) => void;
    clearDraft: (userUID: string, projectUID: string, cardUID: string) => void;
}

const getCommentDraftStorageKey = (userUID: string, projectUID: string, cardUID: string) => `comment-${userUID}-${projectUID}-${cardUID}`;

const useCardCommentDraftStore = create(
    immer<ICardCommentDraftStore>((set, get) => ({
        draftMap: {},
        getDraft: (userUID, projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            if (key in get().draftMap) {
                return get().draftMap[key] ?? "";
            }
            try {
                return sessionStorage.getItem(key) ?? "";
            } catch {
                return "";
            }
        },
        saveDraft: (userUID, projectUID, cardUID, content) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = content;
            });

            try {
                if (content.length > 0) sessionStorage.setItem(key, content);
                else sessionStorage.removeItem(key);
            } catch {
                // Keep the account-scoped in-memory draft when storage is unavailable.
            }
        },
        clearDraft: (userUID, projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(userUID, projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = "";
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
