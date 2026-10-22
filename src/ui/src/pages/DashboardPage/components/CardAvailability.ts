import { isAxiosError } from "axios";
import { api } from "@/core/helpers/Api";

export interface CardAvailabilityPair {
    project_uid: string;
    card_uid: string;
}
export const cardAvailabilityKey = (card: CardAvailabilityPair): string => `${card.project_uid}:${card.card_uid}`;

// No resource or host cache: every validation resolves current server authority.
export async function getAvailableCards(
    cards: CardAvailabilityPair[],
    signal: AbortSignal
): Promise<{ checked: Set<string>; available: Set<string> } | null> {
    const pairs = [...new Map(cards.map((card) => [cardAvailabilityKey(card), card])).values()];
    const checked = new Set<string>();
    const available = new Set<string>();
    const evidence = () => (signal.aborted ? null : { checked, available });
    const record = (requested: CardAvailabilityPair[], result: unknown): boolean => {
        if (!Array.isArray(result)) return false;
        const requestedKeys = new Set(requested.map(cardAvailabilityKey));
        const visible = new Set<string>();
        for (const card of result) {
            if (!card || typeof card.project_uid !== "string" || typeof card.card_uid !== "string") return false;
            const key = cardAvailabilityKey(card);
            if (!requestedKeys.has(key)) return false;
            visible.add(key);
        }
        requestedKeys.forEach((key) => checked.add(key));
        visible.forEach((key) => available.add(key));
        return true;
    };
    const config = { signal, env: { interceptToast: true } as never };
    try {
        for (let offset = 0; offset < pairs.length; offset += 200) {
            if (signal.aborted) return null;
            const batch = pairs.slice(offset, offset + 200);
            try {
                const response = await api.post<{ cards: unknown }>("/dashboard/cards/available", { cards: batch }, config);
                if (!record(batch, response.data.cards)) return evidence();
            } catch (error) {
                if (signal.aborted || !isAxiosError(error) || ![404, 405].includes(error.response?.status ?? 0)) return evidence();
                // Rolling upgrades: only a known missing endpoint permits bounded legacy probes.
                const groups = new Map<string, string[]>();
                batch.forEach(({ project_uid, card_uid }) => groups.set(project_uid, [...(groups.get(project_uid) ?? []), card_uid]));
                for (const [project_uid, card_uids] of groups) {
                    if (signal.aborted) return null;
                    const requested = card_uids.map((card_uid) => ({ project_uid, card_uid }));
                    try {
                        const response = await api.post<{ card_uids: unknown }>(
                            `/board/${encodeURIComponent(project_uid)}/cards/available`,
                            { card_uids },
                            config
                        );
                        if (!Array.isArray(response.data.card_uids) || !response.data.card_uids.every((uid) => typeof uid === "string"))
                            return evidence();
                        if (
                            !record(
                                requested,
                                response.data.card_uids.map((card_uid: string) => ({ project_uid, card_uid }))
                            )
                        )
                            return evidence();
                    } catch (legacyError) {
                        if (signal.aborted || !isAxiosError(legacyError) || legacyError.response?.status !== 403) return evidence();
                        record(requested, []);
                    }
                }
            }
        }
        return evidence();
    } catch {
        return evidence();
    }
}
