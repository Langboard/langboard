import { useRef } from "react";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";
import useAuthStore from "@/core/stores/AuthStore";
import { isCardLocationUID, parseCardLocation } from "./CardLocationResolver";

export default function useResolveCardLocation(projectUID: string, cardUID: string | undefined, paneID: string | undefined, routeKey: string) {
    const { query } = useQueryMutation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const scope = JSON.stringify([projectUID, cardUID, paneID, routeKey, actorUID, session]);
    const currentScope = useRef(scope);
    currentScope.current = scope;
    return query(
        ["resolve-card-location", projectUID, cardUID, actorUID, session, paneID, routeKey],
        async ({ signal }) => {
            if (!isCardLocationUID(projectUID) || !isCardLocationUID(cardUID)) throw new Error("Invalid card location");
            const response = await api.get(Utils.String.format(Routing.API.BOARD.CARD.GET_DETAILS, { uid: projectUID, card_uid: cardUID }), {
                signal,
                params: { location_only: true },
                timeout: 15_000,
                env: { interceptToast: true } as never,
            });
            signal.throwIfAborted();
            if (response.status !== 200) throw new Error("Invalid card location status");
            const auth = useAuthStore.getState();
            if (!actorUID || auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session || currentScope.current !== scope) {
                throw new Error("Card location request no longer authorized");
            }
            // Location preflight never creates card, project, or attachment models.
            return parseCardLocation(response.data, cardUID);
        },
        {
            enabled: !!cardUID && !!actorUID,
            retry: false,
            staleTime: 0,
            gcTime: 0,
            refetchOnWindowFocus: false,
            refetchOnReconnect: false,
        }
    );
}
