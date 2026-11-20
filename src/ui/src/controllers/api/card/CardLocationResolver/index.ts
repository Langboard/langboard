export interface ICardLocation {
    canonical_project_uid: string;
    card_uid: string;
}

// Native Snowflake short codes are exactly eleven base62 characters.
export const isCardLocationUID = (value: unknown): value is string => typeof value === "string" && /^[a-zA-Z0-9]{11}$/.test(value);

export function parseCardLocation(value: unknown, cardUID: string): ICardLocation {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid card location");
    const location = value as Record<string, unknown>;
    if (
        Object.keys(location).length !== 2 ||
        !isCardLocationUID(location.canonical_project_uid) ||
        !isCardLocationUID(location.card_uid) ||
        location.card_uid !== cardUID
    ) {
        throw new Error("Invalid card location");
    }
    return { canonical_project_uid: location.canonical_project_uid, card_uid: location.card_uid };
}
