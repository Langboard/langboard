import { Routing, SocketEvents } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { api } from "@/core/helpers/Api";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { ESocketTopic } from "@langboard/core/enums";
import syncCardRelationships, { ICardRelationshipsUpdatedRawResponse } from "@/controllers/socket/card/syncCardRelationships";
import { queryClient } from "@/core/helpers/QueryClient";
import useAuthStore from "@/core/stores/AuthStore";
import { resetWorkspaceSummaryQueries, workspaceSummaryAuthScope } from "@/controllers/api/WorkspaceSummaryInvalidation";

export interface IUseCardRelationshipsUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    summaryOnly?: boolean;
}

const useCardRelationshipsUpdatedHandlers = ({ callback, projectUID, summaryOnly }: IUseCardRelationshipsUpdatedHandlersProps) => {
    // Model constructors register this factory outside React render.
    const readVersions = new Map<string, number>();
    return useSocketHandler<{}, ICardRelationshipsUpdatedRawResponse & { relationships_invalidated?: boolean }>({
        topic: ESocketTopic.Board,
        topicId: projectUID,
        eventKey: `board-card-relationships-updated-${projectUID}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.CARD.RELATIONSHIPS_UPDATED,
            params: { uid: projectUID },
            callback,
            responseConverter: async (data) => {
                resetWorkspaceSummaryQueries(queryClient, "card-relation-summaries", projectUID, data, data.card_uid);
                if (summaryOnly) return {};
                const authScope = workspaceSummaryAuthScope(useAuthStore.getState());
                const version = (readVersions.get(data.card_uid) ?? 0) + 1;
                readVersions.set(data.card_uid, version);
                if (data.relationships_invalidated) {
                    // Remove the previous audience snapshot before rechecking current access.
                    syncCardRelationships({ card_uid: data.card_uid, relationships: [] });
                    try {
                        const url = Utils.String.format(Routing.API.BOARD.CARD.UPDATE_RELATIONSHIPS, {
                            uid: projectUID,
                            card_uid: data.card_uid,
                        });
                        const response = await api.get(url);
                        if (readVersions.get(data.card_uid) === version && workspaceSummaryAuthScope(useAuthStore.getState()) === authScope) {
                            syncCardRelationships({ card_uid: data.card_uid, relationships: response.data.relationships });
                        }
                    } catch {
                        // A failed authorized read must not restore a queued relationship snapshot.
                    }
                } else {
                    syncCardRelationships(data);
                }
                return {};
            },
        },
    });
};

export default useCardRelationshipsUpdatedHandlers;
