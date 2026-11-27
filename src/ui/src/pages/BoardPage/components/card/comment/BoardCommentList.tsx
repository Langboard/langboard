import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import InfiniteScroller from "@/components/InfiniteScroller";
import useGetCardComments from "@/controllers/api/card/comment/useGetCardComments";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useInfiniteScrollPager from "@/core/hooks/useInfiniteScrollPager";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCardComment } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { ROUTES } from "@/core/routing/constants";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import BoardComment, { SkeletonBoardComment } from "@/pages/BoardPage/components/card/comment/BoardComment";
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";

export function SkeletonBoardCommentList() {
    return (
        <Box pb="2.5">
            <Flex direction="col" gap="4">
                <SkeletonBoardComment />
                <SkeletonBoardComment />
                <SkeletonBoardComment />
                <SkeletonBoardComment />
                <SkeletonBoardComment />
            </Flex>
        </Box>
    );
}

export interface IBoardCommentListProps {
    scrollableRef?: React.RefObject<HTMLDivElement | null>;
    className?: string;
}

function BoardCommentList({ scrollableRef, className }: IBoardCommentListProps): React.JSX.Element {
    const { projectUID, card } = useBoardCard();
    const { data: commentsData, error, isFetching } = useGetCardComments({ project_uid: projectUID, card_uid: card.uid });
    const navigate = usePageNavigateRef();

    useEffect(() => {
        if (!error) {
            return;
        }

        const { handle } = setupApiErrorHandler({
            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
            },
            [EHttpStatus.HTTP_404_NOT_FOUND]: {
                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { replace: true }),
            },
        });

        handle(error);
    }, [error]);

    return (
        <>
            {!commentsData || isFetching ? (
                <SkeletonBoardCommentList />
            ) : (
                <BoardCommentListResult scrollableRef={scrollableRef} className={className} />
            )}
        </>
    );
}

function BoardCommentListResult({ scrollableRef, className }: IBoardCommentListProps): React.JSX.Element {
    const { card, viewportRef } = useBoardCard();
    const [t] = useTranslation();
    const PAGE_SIZE = 10;
    const flatComments = ProjectCardComment.Model.useModels((model) => model.card_uid === card.uid);
    const sortedComments = useMemo(() => flatComments.sort((a, b) => b.updated_at.getTime() - a.updated_at.getTime()), [flatComments]);
    const { items: comments, nextPage, forceUpdate, hasMore } = useInfiniteScrollPager({ allItems: sortedComments, size: PAGE_SIZE });

    const deletedComment = (commentUID: string) => {
        const index = flatComments.findIndex((c) => c.uid === commentUID);
        if (index === -1) {
            return;
        }

        flatComments.splice(index, 1);
        forceUpdate();
    };

    return (
        <InfiniteScroller.NoVirtual
            scrollable={() => scrollableRef?.current || viewportRef.current}
            loadMore={nextPage}
            loader={<SkeletonBoardComment key={Utils.String.Token.shortUUID()} />}
            hasMore={hasMore}
            className={className || "pb-2.5"}
        >
            {comments.length === 0 && (
                <Box textSize="sm" className="text-accent-foreground/50">
                    {t("card.No comments")}
                </Box>
            )}
            <Flex direction="col" gap="4">
                {comments.map((comment) => {
                    return <BoardComment key={`${card.uid}-${comment.uid}`} comment={comment} deletedComment={deletedComment} />;
                })}
            </Flex>
        </InfiniteScroller.NoVirtual>
    );
}

export default BoardCommentList;
