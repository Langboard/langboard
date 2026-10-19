import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

/**
 * Universal draft autosave store for card/card-comment authoring surfaces.
 *
 * Scope key: lb-draft-v1:{userUID}:{projectUID}:{kind}:{scopeUID}
 * - kind: "card-new" | "card-edit" | "comment-new" | "comment-edit"
 * - scopeUID: card UID for card/comment scopes, comment UID for comment-edit,
 *   "new" for fresh cards/comments created from scratch.
 *
 * Drafts survive tab/window close (localStorage) and are isolated per user,
 * project, card and draft kind, so account switches never mix drafts.
 */

export type TDraftKind = "card-new" | "card-edit" | "comment-new" | "comment-edit";

export interface IDraftScope {
    userUID: string;
    projectUID: string;
    kind: TDraftKind;
    /** Card UID, comment UID, or "new" for unsaved surfaces. */
    scopeUID: string;
}

export interface IDraftPayload {
    content: string;
    /** Epoch ms of the last autosave. */
    savedAt: number;
}

interface ICardDraftStore {
    cache: Record<string, IDraftPayload | undefined>;
    saveDraft: (scope: IDraftScope, content: string) => void;
    getDraft: (scope: IDraftScope) => IDraftPayload | null;
    clearDraft: (scope: IDraftScope) => void;
    clearAllDraftsForUser: (userUID: string) => void;
}

const DRAFT_PREFIX = "lb-draft-v1";
/** Drafts older than this are treated as stale and not restored. */
export const DRAFT_TTL_MS = 14 * 24 * 60 * 60 * 1000;

const buildScopeKey = (scope: IDraftScope) =>
    `${DRAFT_PREFIX}:${scope.userUID}:${scope.projectUID}:${scope.kind}:${scope.scopeUID}`;

const readStoredDraft = (key: string): IDraftPayload | null => {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) {
            return null;
        }
        const parsed = JSON.parse(raw) as Partial<IDraftPayload>;
        if (typeof parsed.content !== "string" || typeof parsed.savedAt !== "number") {
            localStorage.removeItem(key);
            return null;
        }
        return { content: parsed.content, savedAt: parsed.savedAt };
    } catch {
        return null;
    }
};

const useCardDraftStore = create(
    immer<ICardDraftStore>((set, get) => ({
        cache: {},
        saveDraft: (scope, content) => {
            if (content.length === 0) {
                get().clearDraft(scope);
                return;
            }
            const key = buildScopeKey(scope);
            const payload: IDraftPayload = { content, savedAt: Date.now() };
            set((state) => {
                state.cache[key] = payload;
            });
            try {
                localStorage.setItem(key, JSON.stringify(payload));
            } catch {
                // Storage quota/private-mode failures must never block typing.
            }
        },
        getDraft: (scope) => {
            const key = buildScopeKey(scope);
            const cached = get().cache[key];
            const payload = cached ?? readStoredDraft(key);
            if (!payload) {
                return null;
            }
            if (Date.now() - payload.savedAt > DRAFT_TTL_MS) {
                get().clearDraft(scope);
                return null;
            }
            return payload;
        },
        clearDraft: (scope) => {
            const key = buildScopeKey(scope);
            set((state) => {
                delete state.cache[key];
            });
            try {
                localStorage.removeItem(key);
            } catch {
                // Best effort only.
            }
        },
        clearAllDraftsForUser: (userUID) => {
            // Keep only this user's drafts; discard drafts left by other accounts.
            set((state) => {
                for (const key of Object.keys(state.cache)) {
                    if (!key.startsWith(`${DRAFT_PREFIX}:${userUID}:`)) {
                        delete state.cache[key];
                    }
                }
            });
            try {
                const stale: string[] = [];
                for (let i = 0; i < localStorage.length; i += 1) {
                    const key = localStorage.key(i);
                    if (key && key.startsWith(`${DRAFT_PREFIX}:`)) {
                        stale.push(key);
                    }
                }
                for (const key of stale) {
                    if (!key.startsWith(`${DRAFT_PREFIX}:${userUID}:`)) {
                        localStorage.removeItem(key);
                    }
                }
            } catch {
                // Best effort only.
            }
        },
    }))
);

export const getCardDraftStore = () => useCardDraftStore.getState();

export default useCardDraftStore;
