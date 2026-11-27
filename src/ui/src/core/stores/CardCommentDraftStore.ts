import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

interface ICardCommentDraftStore {
    draftMap: Record<string, string | undefined>;
    getDraft: (projectUID: string, cardUID: string) => string;
    saveDraft: (projectUID: string, cardUID: string, content: string) => void;
    clearDraft: (projectUID: string, cardUID: string) => void;
}

const getCommentDraftStorageKey = (projectUID: string, cardUID: string) => `comment-${projectUID}-${cardUID}`;

const useCardCommentDraftStore = create(
    immer<ICardCommentDraftStore>((set, get) => ({
        draftMap: {},
        getDraft: (projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(projectUID, cardUID);
            if (key in get().draftMap) {
                return get().draftMap[key] ?? "";
            }
            return sessionStorage.getItem(key) ?? "";
        },
        saveDraft: (projectUID, cardUID, content) => {
            const key = getCommentDraftStorageKey(projectUID, cardUID);
            set((state) => {
                state.draftMap[key] = content;
            });

            if (content.length > 0) {
                sessionStorage.setItem(key, content);
                return;
            }

            sessionStorage.removeItem(key);
        },
        clearDraft: (projectUID, cardUID) => {
            const key = getCommentDraftStorageKey(projectUID, cardUID);
            set((state) => {
                delete state.draftMap[key];
            });
            sessionStorage.removeItem(key);
        },
    }))
);

export const getCardCommentDraftStore = () => useCardCommentDraftStore.getState();

export default useCardCommentDraftStore;
