import type { ICardCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
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
    anchor?: ICardCommentAnchor | null;
    request_uid?: string;
    /** Epoch ms of the last autosave. */
    savedAt: number;
    /** Retained for native comment migration so an old session copy cannot return. */
    cleared?: true;
}

interface ICardDraftStore {
    cache: Record<string, IDraftPayload | undefined>;
    saveDraft: (scope: IDraftScope, content: string, metadata?: Pick<IDraftPayload, "anchor" | "request_uid">) => void;
    getDraft: (scope: IDraftScope) => IDraftPayload | null;
    clearDraft: (scope: IDraftScope, retainTombstone?: boolean) => void;
}

const DRAFT_PREFIX = "lb-draft-v1";
/** Drafts older than this are treated as stale and not restored. */
export const DRAFT_TTL_MS = 14 * 24 * 60 * 60 * 1000;

const buildScopeKey = (scope: IDraftScope) => `${DRAFT_PREFIX}:${scope.userUID}:${scope.projectUID}:${scope.kind}:${scope.scopeUID}`;

/** Compose the exact storage/cache key for a draft scope (for reactive cache subscriptions). */
export const buildDraftScopeKey = buildScopeKey;

/** Persist without notifying subscribers; safe for synchronous legacy restoration. */
export const persistCardDraft = (scope: IDraftScope, payload: IDraftPayload): boolean => {
    try {
        localStorage.setItem(buildScopeKey(scope), JSON.stringify(payload));
        return true;
    } catch {
        return false;
    }
};

const readStoredDraft = (key: string): IDraftPayload | null => {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) {
            return null;
        }
        const parsed = JSON.parse(raw) as Partial<IDraftPayload>;
        if (!parsed || typeof parsed.content !== "string" || typeof parsed.savedAt !== "number" || !Number.isFinite(parsed.savedAt)) {
            localStorage.removeItem(key);
            return null;
        }
        return {
            content: parsed.content,
            savedAt: parsed.savedAt,
            ...(parsed.cleared === true ? { cleared: true as const } : {}),
            ...(parsed.anchor === null || (typeof parsed.anchor === "object" && !Array.isArray(parsed.anchor)) ? { anchor: parsed.anchor } : {}),
            ...(typeof parsed.request_uid === "string" ? { request_uid: parsed.request_uid } : {}),
        };
    } catch {
        return null;
    }
};

const useCardDraftStore = create(
    immer<ICardDraftStore>((set, get) => ({
        cache: {},
        saveDraft: (scope, content, metadata) => {
            if (content.length === 0 && !metadata?.anchor && !metadata?.request_uid && scope.kind !== "card-edit") {
                get().clearDraft(scope);
                return;
            }
            const key = buildScopeKey(scope);
            const payload: IDraftPayload = { content, ...metadata, savedAt: Date.now() };
            set((state) => {
                state.cache[key] = payload;
            });
            persistCardDraft(scope, payload);
        },
        getDraft: (scope) => {
            const key = buildScopeKey(scope);
            const cached = get().cache[key];
            const payload = cached ?? readStoredDraft(key);
            if (!payload || payload.cleared) {
                return null;
            }
            if (Date.now() - payload.savedAt > DRAFT_TTL_MS) {
                get().clearDraft(scope);
                return null;
            }
            return payload;
        },
        clearDraft: (scope, retainTombstone = false) => {
            const key = buildScopeKey(scope);
            if (retainTombstone) {
                const tombstone: IDraftPayload = { content: "", savedAt: Date.now(), cleared: true };
                set((state) => {
                    state.cache[key] = tombstone;
                });
                persistCardDraft(scope, tombstone);
                return;
            }
            try {
                localStorage.removeItem(key);
                set((state) => {
                    delete state.cache[key];
                });
            } catch {
                // Persisted removal failed: keep an in-memory cleared tombstone so
                // getDraft() cannot re-read and resurrect the old persisted value.
                const tombstone: IDraftPayload = { content: "", savedAt: Date.now(), cleared: true };
                set((state) => {
                    state.cache[key] = tombstone;
                });
                try {
                    localStorage.setItem(key, JSON.stringify(tombstone));
                } catch {
                    // Fully denied storage can only retain the tombstone in memory.
                }
            }
        },
    }))
);

export const getCardDraftStore = () => useCardDraftStore.getState();

export default useCardDraftStore;
