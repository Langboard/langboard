export interface ILabelToggleMeta {
    checked: boolean;
    labelUID: string;
    updatedAt: number;
}

/** Keep complete peer metadata and the first value on equal timestamps. */
export function latestLabelStates<T extends { value: unknown }>(metadata: readonly T[]) {
    const states = Object.create(null) as Record<string, T & { value: ILabelToggleMeta }>;
    for (const meta of metadata) {
        const value = meta.value;
        if (!value || typeof value !== "object") continue;
        const { labelUID, checked, updatedAt } = value as Record<string, unknown>;
        if (typeof labelUID !== "string" || typeof checked !== "boolean" || typeof updatedAt !== "number") continue;
        const previous = states[labelUID];
        if (!previous || previous.value.updatedAt < updatedAt) states[labelUID] = meta as T & { value: ILabelToggleMeta };
    }
    return states;
}
