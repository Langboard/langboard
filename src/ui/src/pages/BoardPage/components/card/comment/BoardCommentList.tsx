import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import useGetCardTimeline from "@/controllers/api/card/comment/useGetCardTimeline";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCardComment } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import BoardComment, { SkeletonBoardComment } from "@/pages/BoardPage/components/card/comment/BoardComment";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import CardColumnEvent from "../CardColumnEvent";
import { isAxiosError } from "axios";

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

function BoardCommentList({ className }: IBoardCommentListProps): React.JSX.Element | null {
    const { card } = useBoardCard();
    const { entries, error, isPending, isFetching, hasNextPage, fetchNextPage, refresh, editing } = useGetCardTimeline();
    ProjectCardComment.Model.useModels((model) => model.card_uid === card.uid, [card]);
    const navigate = usePageNavigateRef();
    const [t] = useTranslation();

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

    if (isAxiosError(error) && [403, 404].includes(error.response?.status ?? 0)) return null;
    if (error && !entries.length) return null;
    if (isPending) return <SkeletonBoardCommentList />;
    return (
        <Box className={className || "pb-2.5"}>
            {entries.length === 0 && (
                <Box textSize="sm" className="text-accent-foreground/50">
                    {t("card.No comments")}
                </Box>
            )}
            <Flex direction="col" gap="4">
                {entries.map((entry) => {
                    if (entry.kind === "activity")
                        return (
                            <Flex key={entry.evidence_ref} items="center" gap="1" className="min-w-0 text-[11px] text-muted-foreground/80">
                                <IconComponent icon="history" size="3" aria-hidden="true" />
                                <CardColumnEvent event={entry.data} showTransition />
                            </Flex>
                        );
                    const comment = ProjectCardComment.Model.getModel(entry.uid);
                    return comment ? <BoardComment key={entry.evidence_ref} comment={comment} deletedComment={refresh} /> : null;
                })}
            </Flex>
            {hasNextPage && (
                <Button
                    type="button"
                    variant="ghost"
                    className="mt-3 w-full"
                    disabled={isFetching || editing}
                    onClick={() => void fetchNextPage({ cancelRefetch: false })}
                >
                    {t("card.Older history")}
                </Button>
            )}
        </Box>
    );
}

export default BoardCommentList;
