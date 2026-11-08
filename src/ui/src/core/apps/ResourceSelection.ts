export class ResourceSelectionConflict extends Error {}

/** App-provided display suggestions, never connection identity or consent authority. */
export interface ResourceCandidate {
    resource_type: string;
    external_resource_id: string;
    label: string;
}

export interface ResourceSnapshot {
    app_revision: string;
    binding_uid: string;
    binding_revision: string;
    resource_types: string[];
    items: { resource_type: string; external_resource_id: string; access_revision: number; selected: boolean }[];
}

export function readResourceCandidates(value: unknown): ResourceCandidate[] {
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some((key) => key !== "candidates"))
        throw new Error("Invalid resource selection request");
    const candidates = (value as { candidates?: unknown }).candidates;
    if (!Array.isArray(candidates) || candidates.length < 1 || candidates.length > 20) throw new Error("Invalid resource candidates");
    const identities = new Set<string>();
    return candidates.map((candidate) => {
        if (
            !candidate ||
            typeof candidate !== "object" ||
            Array.isArray(candidate) ||
            Object.keys(candidate as Record<string, unknown>).some((key) => !["resource_type", "external_resource_id", "label"].includes(key))
        )
            throw new Error("Invalid resource candidate");
        for (const [key, limit] of [
            ["resource_type", 64],
            ["external_resource_id", 200],
            ["label", 160],
        ] as const) {
            const text = candidate[key];
            if (
                typeof text !== "string" ||
                !text.length ||
                text.length > limit ||
                text.trim() !== text ||
                Array.from(text).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
            )
                throw new Error("Invalid resource candidate field");
        }
        const identity = JSON.stringify([candidate.resource_type, candidate.external_resource_id]);
        if (identities.has(identity)) throw new Error("Duplicate resource candidate");
        identities.add(identity);
        return { resource_type: candidate.resource_type, external_resource_id: candidate.external_resource_id, label: candidate.label };
    });
}

export function assertResourceSelectionCurrent(
    shown: ResourceSnapshot,
    fresh: ResourceSnapshot,
    candidate: Pick<ResourceCandidate, "resource_type" | "external_resource_id">,
    authority?: { app_revision: string; binding_revision: string },
    selecting = true
) {
    if (
        fresh.app_revision !== shown.app_revision ||
        fresh.binding_uid !== shown.binding_uid ||
        fresh.binding_revision !== shown.binding_revision ||
        (selecting && !fresh.resource_types.includes(candidate.resource_type)) ||
        (authority && (fresh.app_revision !== authority.app_revision || fresh.binding_revision !== authority.binding_revision))
    )
        throw new ResourceSelectionConflict("Resource authority changed; review the current selection");
    const find = (snapshot: ResourceSnapshot) =>
        snapshot.items.find((item) => item.resource_type === candidate.resource_type && item.external_resource_id === candidate.external_resource_id);
    const previous = find(shown),
        current = find(fresh);
    if (previous?.access_revision !== current?.access_revision || previous?.selected !== current?.selected)
        throw new ResourceSelectionConflict("Resource selection changed; review its current state");
}

/** One human confirmation: no parallel dispatch and no replay after an unobserved result. */
export async function confirmResourceWrite(
    state: { busy: boolean; needsRefresh: boolean },
    signal: AbortSignal,
    revalidate: () => Promise<void>,
    write: () => Promise<unknown>,
    isCurrent: () => boolean = () => true
) {
    if (state.busy || state.needsRefresh) throw new Error("Resource confirmation requires a fresh review");
    signal.throwIfAborted();
    if (!isCurrent()) throw new ResourceSelectionConflict("Resource scope changed");
    state.busy = true;
    let dispatched = false;
    try {
        await revalidate();
        signal.throwIfAborted();
        if (!isCurrent()) throw new ResourceSelectionConflict("Resource scope changed");
        dispatched = true;
        await write();
        signal.throwIfAborted();
        if (!isCurrent()) throw new ResourceSelectionConflict("Resource scope changed");
    } catch (error) {
        if (dispatched) state.needsRefresh = true;
        throw error;
    } finally {
        state.busy = false;
    }
}
