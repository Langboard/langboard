import Card from "@/components/base/Card";
import type { CommentCompaction } from "@/controllers/api/card/comment/CommentCompaction.types";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import useCommentCompaction from "@/controllers/api/card/comment/useCommentCompaction";
import useGetCardTimeline from "@/controllers/api/card/comment/useGetCardTimeline";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCardComment } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import BoardComment, { SkeletonBoardComment } from "@/pages/BoardPage/components/card/comment/BoardComment";
import { useEffect, useState } from "react";
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
    compactionUID?: string;
    evidenceRef?: string;
    onEvidenceError?: (error: unknown) => void;
}

function BoardCommentList({ className, compactionUID, evidenceRef, onEvidenceError }: IBoardCommentListProps): React.JSX.Element | null {
    const { card } = useBoardCard();
    const {
        entries,
        error: queryError,
        isPending,
        isFetching,
        isFetchingNextPage,
        hasNextPage,
        fetchNextPage,
        refresh,
        editing,
        eligibility,
        inFlight,
    } = useGetCardTimeline(compactionUID);
    const [evidenceError, setEvidenceError] = useState<unknown>(null);
    const compaction = useCommentCompaction(eligibility, inFlight, refresh);
    const error = queryError ?? evidenceError ?? compaction.error;
    ProjectCardComment.Model.useModels((model) => model.card_uid === card.uid, [card]);
    const navigate = usePageNavigateRef();
    const [t] = useTranslation();

    useEffect(() => {
        if (queryError && compactionUID) onEvidenceError?.(queryError);
    }, [queryError, compactionUID, onEvidenceError]);

    useEffect(() => {
        if (!evidenceRef || !compactionUID || error || isFetching || editing) return;
        if (entries.some((entry) => entry.evidence_ref === evidenceRef)) {
            const element = document.getElementById(`card-evidence-${compactionUID}-${evidenceRef}`);
            element?.scrollIntoView({ block: "nearest", behavior: "smooth" });
            element?.focus({ preventScroll: true });
        } else if (hasNextPage) void fetchNextPage({ cancelRefetch: false });
    }, [evidenceRef, compactionUID, entries, error, isFetching, editing, hasNextPage, fetchNextPage]);

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
    if (error && compactionUID)
        return (
            <Box role="alert" textSize="sm">
                {t("card.Summary evidence changed. Reload comments.")}
            </Box>
        );
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
                    if (entry.kind === "compaction")
                        return (
                            <CommentCompactionBlock key={entry.evidence_ref} block={entry.data} refresh={refresh} onAccessError={setEvidenceError} />
                        );
                    const evidenceProps = compactionUID ? { id: `card-evidence-${compactionUID}-${entry.evidence_ref}`, tabIndex: -1 } : {};
                    if (entry.kind === "activity")
                        return (
                            <Flex
                                key={entry.evidence_ref}
                                {...evidenceProps}
                                items="center"
                                gap="1"
                                className="min-w-0 rounded text-[11px] text-muted-foreground/80 focus:ring-2 focus:ring-primary"
                            >
                                <IconComponent icon="history" size="3" aria-hidden="true" />
                                <CardColumnEvent event={entry.data} showTransition />
                            </Flex>
                        );
                    const comment = ProjectCardComment.Model.getModel(entry.uid);
                    return comment ? (
                        <div
                            key={entry.evidence_ref}
                            {...evidenceProps}
                            className="min-w-0 rounded focus:outline-none focus:ring-2 focus:ring-primary"
                        >
                            <BoardComment comment={comment} deletedComment={refresh} />
                        </div>
                    ) : null;
                })}
            </Flex>
            {!compactionUID &&
                eligibility?.can_generate &&
                ((eligibility.provider_configured && eligibility.needs_compaction) ||
                    (compaction.record && compaction.record.status !== "completed")) && (
                    <div
                        className="sticky bottom-0 z-10 mt-3 flex flex-wrap items-center gap-2 rounded-lg border bg-background/95 p-2 shadow-lg"
                        aria-live="polite"
                    >
                        {compaction.active ? (
                            <>
                                <span className="text-sm">{t("card.Compacting comments")}</span>
                                <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={!compaction.record?.uid || compaction.cancelling}
                                    onClick={() => void compaction.cancel()}
                                >
                                    {t("card.Cancel compaction")}
                                </Button>
                            </>
                        ) : (
                            <>
                                {compaction.record && ["failed", "cancelled", "retry_required", "invalidated"].includes(compaction.record.status) && (
                                    <span role="status" className="text-sm">
                                        {t(`card.Compaction ${compaction.record.status}`)}
                                    </span>
                                )}
                                {compaction.record?.request_key && ["failed", "cancelled", "retry_required"].includes(compaction.record.status) && (
                                    <Button
                                        type="button"
                                        size="sm"
                                        variant="outline"
                                        disabled={editing}
                                        onClick={() => void compaction.generate(true)}
                                    >
                                        {t("card.Retry compaction")}
                                    </Button>
                                )}
                                {eligibility.needs_compaction && eligibility.provider_configured && (
                                    <Button
                                        type="button"
                                        size="sm"
                                        disabled={editing || !eligibility.next_request_key}
                                        onClick={() => void compaction.generate()}
                                    >
                                        {t("card.Compact comments")}
                                    </Button>
                                )}
                            </>
                        )}
                    </div>
                )}
            {hasNextPage && (
                <Button
                    type="button"
                    variant="ghost"
                    className="mt-3 w-full"
                    disabled={isFetchingNextPage || editing}
                    onClick={() => void fetchNextPage()}
                >
                    {t("card.Older history")}
                </Button>
            )}
        </Box>
    );
}

function CommentCompactionBlock({
    block,
    refresh,
    onAccessError,
}: {
    block: CommentCompaction;
    refresh: () => void;
    onAccessError: (error: unknown) => void;
}) {
    const [t] = useTranslation();
    const [expanded, setExpanded] = useState(false);
    const [target, setTarget] = useState<string>();
    const [invalidated, setInvalidated] = useState(false);
    const evidenceError = (error: unknown) => {
        if (isAxiosError(error) && [403, 404].includes(error.response?.status ?? 0)) onAccessError(error);
        if (isAxiosError(error) && error.response?.status === 409) {
            setInvalidated(true);
            refresh();
        }
    };
    const kinds = {
        fact: "Recorded fact",
        reported_success: "Reported success",
        reported_failure: "Reported failure",
        unresolved: "Unresolved",
        conflict: "Conflicting evidence",
    } as const;
    return (
        <Card.Root data-comment-compaction-uid={block.uid} className="min-w-0 overflow-hidden border-primary/30 bg-primary/5">
            <Card.Header className="gap-2 p-3">
                <Card.Title className="text-sm">{t("card.Compacted comments")}</Card.Title>
                <Card.Description>{t("card.AI summary. Check the original evidence.")}</Card.Description>
                {block.generated_by && (
                    <p data-generated-by-uid={block.generated_by.uid} className="break-words text-xs text-muted-foreground">
                        {t("card.Generated by {{name}}", { name: block.generated_by.display_name })}
                    </p>
                )}
            </Card.Header>
            <Card.Content className="space-y-3 px-3 pb-3">
                {invalidated ? (
                    <p role="alert">{t("card.Summary evidence changed. Reload comments.")}</p>
                ) : (
                    block.draft?.claims.map((claim, index) => (
                        <div key={index} className="min-w-0 space-y-1">
                            <p className="text-xs text-muted-foreground">{t(`card.${kinds[claim.kind]}`)}</p>
                            <p className="whitespace-pre-wrap break-words text-sm">{claim.text}</p>
                            <div className="flex flex-wrap gap-1">
                                {claim.evidence_refs.map((ref) => (
                                    <Button
                                        key={ref}
                                        size="sm"
                                        variant="link"
                                        className="h-auto px-1 py-0.5"
                                        onClick={() => {
                                            setExpanded(true);
                                            setTarget(ref);
                                        }}
                                    >
                                        {t("card.Evidence {{number}}", { number: (block.evidence_refs?.indexOf(ref) ?? -1) + 1 })}
                                    </Button>
                                ))}
                            </div>
                        </div>
                    ))
                )}
                {!invalidated && (
                    <Button type="button" variant="outline" size="sm" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
                        {t(expanded ? "card.Hide original comments" : "card.Show original comments")}
                    </Button>
                )}
                {expanded && !invalidated && <BoardCommentList compactionUID={block.uid} evidenceRef={target} onEvidenceError={evidenceError} />}
            </Card.Content>
        </Card.Root>
    );
}

export default BoardCommentList;
