import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { Routing, SocketEvents } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser, ProjectWiki, User } from "@/core/models";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { Utils } from "@langboard/core/utils";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiAssigneesUpdatedRawResponse {
    assigned_members: User.Interface[];
}

export interface IUseBoardWikiAssigneesUpdatedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    wiki: ProjectWiki.TModel;
    currentUser: AuthUser.TModel;
}

const useBoardWikiAssigneesUpdatedHandlers = ({ callback, projectUID, wiki, currentUser }: IUseBoardWikiAssigneesUpdatedHandlersProps) => {
    const socket = useSocketOutsideProvider();

    return useSocketHandler<{}, IBoardWikiAssigneesUpdatedRawResponse>({
        topic: ESocketTopic.BoardWiki,
        topicId: projectUID,
        eventKey: `board-wiki-assignees-updated-${wiki.uid}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.WIKI.ASSIGNEES_UPDATED,
            params: { uid: wiki.uid },
            callback,
            responseConverter: (data) => {
                resetWorkspaceSummaryQueries(queryClient, "wiki-summaries", projectUID, data);
                if (wiki.is_public) {
                    return {};
                }

                if (!currentUser.is_admin && !data.assigned_members.some((member) => member.uid === currentUser.uid)) {
                    wiki.changeToPrivate();
                    return {};
                }

                if (!socket.isSubscribed(ESocketTopic.BoardWikiPrivate, wiki.uid)) {
                    socket.subscribe(ESocketTopic.BoardWikiPrivate, [wiki.uid], () => {
                        api.get(
                            Utils.String.format(Routing.API.BOARD.WIKI.GET_DETAILS, {
                                uid: projectUID,
                                wiki_uid: wiki.uid,
                            })
                        ).then((response) => {
                            ProjectWiki.Model.fromOne(response.data.wiki, true);
                        });
                    });
                } else {
                    wiki.assigned_members = data.assigned_members;
                }

                return {};
            },
        },
    });
};

export default useBoardWikiAssigneesUpdatedHandlers;
