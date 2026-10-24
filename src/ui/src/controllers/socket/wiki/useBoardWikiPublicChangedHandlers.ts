import { queryClient } from "@/core/helpers/QueryClient";
import { resetWorkspaceSummaryQueries } from "@/controllers/api/WorkspaceSummaryInvalidation";
import { SocketEvents } from "@langboard/core/constants";
import useSocketHandler, { IBaseUseSocketHandlersProps } from "@/core/helpers/SocketHandler";
import { AuthUser, ProjectWiki } from "@/core/models";
import { ESocketTopic } from "@langboard/core/enums";

export interface IBoardWikiPublicChangedRawResponse {
    wiki: Pick<ProjectWiki.IStore, "uid" | "is_public" | "assigned_members">;
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
                const needsDetails = wiki.forbidden && data.wiki.is_public;
                // Also whitelist legacy full-wiki events that were queued before deployment.
                // Publicity cannot replace content or roll back an editor generation.
                const metadata = {
                    uid: wiki.uid,
                    created_at: wiki.created_at,
                    updated_at: wiki.updated_at,
                    is_public: data.wiki.is_public,
                    assigned_members: data.wiki.assigned_members ?? [],
                };
                ProjectWiki.Model.fromOne(metadata, true);
                if (!wiki.is_public && !currentUser.is_admin) {
                    if (!wiki.assigned_members.some((member) => member.uid === currentUser.uid)) {
                        wiki.changeToPrivate();
                    }
                }
                if (needsDetails) {
                    // The existing ACL-filtered list query hydrates newly visible wikis.
                    // Keep the forbidden placeholder until that authorized read succeeds.
                    void queryClient.invalidateQueries({ queryKey: [`get-wikis-${projectUID}`], exact: true });
                }
                return {};
            },
        },
    });
};

export default useBoardWikiPublicChangedHandlers;
