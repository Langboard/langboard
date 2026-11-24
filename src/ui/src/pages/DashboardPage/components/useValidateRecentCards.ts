import { useEffect } from "react";
import { getAvailableCards } from "./CardAvailability";
import { removeUnavailableCards } from "./OpenCardsStore";
import type { IOpenCard } from "./OpenCardsData";

export default function useValidateRecentCards(userUID: string, cards: IOpenCard[], active = true): void {
    const identities = JSON.stringify(cards.map((card) => [card.projectUID, card.cardUID]).sort());
    useEffect(() => {
        if (!active || identities === "[]") return;
        const controller = new AbortController();
        let running = false;
        const validate = async () => {
            if (running || controller.signal.aborted) return;
            running = true;
            try {
                const pairs = (JSON.parse(identities) as [string, string][]).map(([project_uid, card_uid]) => ({ project_uid, card_uid }));
                const result = await getAvailableCards(pairs, controller.signal);
                if (!controller.signal.aborted && result !== null) {
                    const { checked, available } = result;
                    if ([...checked].some((key) => !available.has(key))) removeUnavailableCards(userUID, checked, available);
                }
            } finally {
                running = false;
            }
        };
        void validate();
        window.addEventListener("focus", validate);
        window.addEventListener("online", validate);
        return () => {
            controller.abort();
            window.removeEventListener("focus", validate);
            window.removeEventListener("online", validate);
        };
    }, [userUID, identities, active]);
}
