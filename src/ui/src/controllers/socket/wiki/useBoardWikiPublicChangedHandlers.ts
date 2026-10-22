import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser, ProjectWiki } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiPublicChangedRawResponse {
    wiki: ProjectWiki.IStore;
}

export interface IUseBoardWikiPublicChangedHandlersProps extends IBaseUseSocketHandlersProps<{}> {
    projectUID: string;
    wiki: ProjectWiki.TModel;
    currentUser: AuthUser.TModel;
}

const useBoardWikiPublicChangedHandlers = ({ callback, projectUID, currentUser, wiki }: IUseBoardWikiPublicChangedHandlersProps) => {
    return useSocketHandler<{}, IBoardWikiPublicChangedRawResponse>({
        topic: ESocketTopic.BoardWiki,
        topicId: projectUID,
        eventKey: `board-wiki-public-changed-${wiki.uid}`,
        onProps: {
            name: SocketEvents.SERVER.BOARD.WIKI.PUBLIC_CHANGED,
            params: { uid: wiki.uid },
            callback,
            responseConverter: (data) => {
                resetWorkspaceSummaryQueries(queryClient, "wiki-summaries", projectUID, data);
                ProjectWiki.Model.fromOne(data.wiki, true);
                if (!wiki.is_public && !currentUser.is_admin) {
                    if (!wiki.assigned_members.some((member) => member.uid === currentUser.uid)) {
                        wiki.changeToPrivate();
                    }
                }
                return {};
            },
        },
    });
};

export default useBoardWikiPublicChangedHandlers;
