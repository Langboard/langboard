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
    /** Optional new-card body; legacy content remains the title. */
    description?: string;
    anchor?: ICardCommentAnchor | null;
    request_uid?: string;
    /** Saved comment this edit was based on; absent on legacy drafts. */
    base_content?: string;
    base_revision?: string;
    base_updated_at?: string;
    /** Epoch ms of the last autosave. */
    savedAt: number;
    /** Retained for native comment migration so an old session copy cannot return. */
    cleared?: true;
}

interface ICardDraftStore {
    cache: Record<string, IDraftPayload | undefined>;
    saveDraft: (
        scope: IDraftScope,
        content: string,
        metadata?: Pick<IDraftPayload, "anchor" | "request_uid" | "description" | "base_content" | "base_revision" | "base_updated_at">
    ) => void;
    getDraft: (scope: IDraftScope) => IDraftPayload | null;
    clearDraft: (scope: IDraftScope, retainTombstone?: boolean) => void;
}

const DRAFT_PREFIX = "lb-draft-v1";
/** Drafts older than this are treated as stale and not restored. */
export const DRAFT_TTL_MS = 14 * 24 * 60 * 60 * 1000;

const buildScopeKey = (scope: IDraftScope) => `${DRAFT_PREFIX}:${scope.userUID}:${scope.projectUID}:${scope.kind}:${scope.scopeUID}`;

/** Compose the exact storage/cache key for a draft scope (for reactive cache subscriptions). */
export const buildDraftScopeKey = buildScopeKey;

// Failed writes have no durable replacement. Other tabs must not invalidate
// the only remaining copy (including cleared markers) before persistence recovers.
const unpersistedDrafts = new WeakSet<IDraftPayload>();

/** Persist without notifying subscribers; safe for synchronous legacy restoration. */
export const persistCardDraft = (scope: IDraftScope, payload: IDraftPayload): boolean => {
    try {
        localStorage.setItem(buildScopeKey(scope), JSON.stringify(payload));
        unpersistedDrafts.delete(payload);
        return true;
    } catch {
        unpersistedDrafts.add(payload);
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
            ...(typeof parsed.base_content === "string" ? { base_content: parsed.base_content } : {}),
            ...(typeof parsed.base_revision === "string" ? { base_revision: parsed.base_revision } : {}),
            ...(typeof parsed.base_updated_at === "string" && Number.isFinite(Date.parse(parsed.base_updated_at))
                ? { base_updated_at: parsed.base_updated_at }
                : {}),
            ...(parsed.cleared === true ? { cleared: true as const } : {}),
            ...(parsed.anchor === null || (typeof parsed.anchor === "object" && !Array.isArray(parsed.anchor)) ? { anchor: parsed.anchor } : {}),
            ...(typeof parsed.description === "string" ? { description: parsed.description } : {}),
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
            if (
                content.length === 0 &&
                !metadata?.anchor &&
                !metadata?.request_uid &&
                !metadata?.description &&
                scope.kind !== "card-edit" &&
                scope.kind !== "comment-edit"
            ) {
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
                persistCardDraft(scope, tombstone);
            }
        },
    }))
);

// Native storage events supersede committed cached copies from other tabs.
// Preserve failed writes, which have no durable copy to restore.
if (typeof window !== "undefined") {
    const onStorage = (event: StorageEvent) => {
        if (event.key !== null && !event.key.startsWith(`${DRAFT_PREFIX}:`)) return;
        try {
            if (event.storageArea !== localStorage) return;
        } catch {
            // A denied storage getter must preserve this tab's in-memory drafts.
            return;
        }
        const cache = { ...useCardDraftStore.getState().cache };
        for (const [key, payload] of Object.entries(cache)) {
            if ((event.key === null || event.key === key) && (!payload || !unpersistedDrafts.has(payload))) {
                delete cache[key];
            }
        }
        useCardDraftStore.setState({ cache });
    };
    window.addEventListener("storage", onStorage);
    import.meta.hot?.dispose(() => window.removeEventListener("storage", onStorage));
}

export const getCardDraftStore = () => useCardDraftStore.getState();

export default useCardDraftStore;
