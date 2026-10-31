import CardNextAction from "@/pages/BoardPage/components/card/CardNextAction";
import { DecisionProvider } from "./decision/DecisionContext";
import BoardCardDecisionGroup from "./decision/BoardCardDecisionGroup";
import { CARD_BODY_SECTIONS } from "./CardWorkbenchLenses";
import useAuthStore from "@/core/stores/AuthStore";
import BoardCardWhispers from "./BoardCardWhispers";
import CardAppArchiveReceipts from "./CardAppArchiveReceipts";
import CardFlipTray from "./CardFlipTray";
import { useCardFlipStore } from "./CardFlipStore";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import Floating from "@/components/base/Floating";
import IconComponent from "@/components/base/IconComponent";
import ScrollArea from "@/components/base/ScrollArea";
import ShineBorder from "@/components/base/ShineBorder";
import Skeleton from "@/components/base/Skeleton";
import Toast from "@/components/base/Toast";
import useChangeCardDetails from "@/controllers/api/card/useChangeCardDetails";
import useSetCardCompleted from "@/controllers/api/board/useSetCardCompleted";
import useGetCardDetails, { IGetCardDetailsResponse } from "@/controllers/api/card/useGetCardDetails";
import useUnreadChangeNavigation, { highlightUnreadTarget } from "@/pages/BoardPage/components/card/useUnreadChangeNavigation";
import { WORKBENCH_OUTLINE_EVENT } from "@/pages/DashboardPage/components/WorkbenchCommands";

import useReplaceCardContentBlocks from "@/controllers/api/board/useReplaceCardContentBlocks";
import { extractContentBlocks } from "@/pages/BoardPage/components/card/contentBlockSerializer";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { BoardCardProvider, useBoardCard, useBoardCardPanel } from "@/core/providers/BoardCardProvider";
import { useBoardController } from "@/core/providers/BoardController";
import { ROUTES } from "@/core/routing/constants";
import BoardCardActionList from "@/pages/BoardPage/components/card/action/BoardCardActionList";
import BoardCardActionRelationship from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationship";
import BoardCardChecklistGroup from "@/pages/BoardPage/components/card/checklist/BoardCardChecklistGroup";
import { useBoardCardSectionSaveActions } from "@/pages/BoardPage/components/card/BoardCardSectionSaveProvider";
import BoardCardColumnName from "@/pages/BoardPage/components/card/BoardCardColumnName";
import BoardCardDeadline from "@/pages/BoardPage/components/card/BoardCardDeadline";
import BoardCardDescription from "@/pages/BoardPage/components/card/BoardCardDescription";
import BoardCardCheckBody from "@/pages/BoardPage/components/card/BoardCardCheckBody";
import BoardCardAttachmentList from "@/pages/BoardPage/components/card/attachment/BoardCardAttachmentList";
import BoardCardTitle from "@/pages/BoardPage/components/card/BoardCardTitle";
import BoardCardPriority from "@/pages/BoardPage/components/card/BoardCardPriority";
import { getCardVisualSignals } from "@/pages/BoardPage/components/board/CardVisualSignals";
import useCardMotionVisibility from "@/core/hooks/useCardMotionVisibility";
import { useBoard } from "@/core/providers/BoardProvider";
import BoardCommentForm from "@/pages/BoardPage/components/card/comment/BoardCommentForm";
import BoardCommentList from "@/pages/BoardPage/components/card/comment/BoardCommentList";
import { forwardRef, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import BoardCardMemberList from "@/pages/BoardPage/components/card/BoardCardMemberList";
import BoardCardVisibility from "./BoardCardVisibility";
import CardTypeBadges from "@/components/CardTypeBadges";
import { cardVisibilityPresentation } from "@/core/cards/CardPresentation";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { useSocket } from "@/core/providers/SocketProvider";
import BoardCardLabelList from "@/pages/BoardPage/components/card/label/BoardCardLabelList";
import { AuthUser, ProjectCard, ProjectCardAttachment, ProjectChecklist } from "@/core/models";
import useCardDeletedHandlers from "@/controllers/socket/card/useCardDeletedHandlers";
import { SocketEvents } from "@langboard/core/constants";
import { EHttpStatus, ESocketTopic } from "@langboard/core/enums";
import { getEditorStore } from "@/core/stores/EditorStore";
import { useHasRunningBot } from "@/core/stores/BotStatusStore";
import { cn } from "@/core/utils/ComponentUtils";
import { useBoardChat } from "@/core/providers/BoardChatProvider";
import BoardTaskMetadataSection from "@/pages/BoardPage/components/task/BoardTaskMetadataSection";
import BoardCardMirror from "./mirror/BoardCardMirror";
import BoardLinkedWikiCard from "@/pages/BoardPage/components/card/BoardLinkedWikiCard";
import useCardLinkedResourceChangedHandlers from "@/controllers/socket/card/useCardLinkedResourceChangedHandlers";
import useSwitchSocketHandlers from "@/core/hooks/useSwitchSocketHandlers";
import CardTimestamps from "@/pages/BoardPage/components/card/CardTimestamps";
import CardColumnHistory from "@/pages/BoardPage/components/card/CardColumnHistory";
import useBoardChecklistProgressChangedHandlers from "@/controllers/socket/card/checklist/useBoardChecklistProgressChangedHandlers";
import { useQueryClient } from "@tanstack/react-query";
import { closeCard, focusCard } from "@/pages/DashboardPage/components/OpenCardsStore";
import {
    clampCommentPanelWidth,
    DEFAULT_COMMENT_PANEL_WIDTH,
    getCommentPanelWidthBounds,
    MAX_COMMENT_PANEL_WIDTH,
    MIN_COMMENT_PANEL_WIDTH,
} from "@/pages/BoardPage/components/card/comment/CommentPanelWidth";

const COMMENT_PANEL_WIDTH_KEY = "langboard-comment-panel-width";

function readCommentPanelWidth(): number {
    try {
        const saved = Number(window.sessionStorage.getItem(COMMENT_PANEL_WIDTH_KEY));
        return saved > 0 ? Math.min(MAX_COMMENT_PANEL_WIDTH, Math.max(MIN_COMMENT_PANEL_WIDTH, saved)) : DEFAULT_COMMENT_PANEL_WIDTH;
    } catch {
        return DEFAULT_COMMENT_PANEL_WIDTH;
    }
}

function saveCommentPanelWidth(width: number): void {
    try {
        window.sessionStorage.setItem(COMMENT_PANEL_WIDTH_KEY, String(width));
    } catch {
        // Resizing still works when browser storage is disabled.
    }
}

export interface IBoardCardProps {
    projectUID: string;
    cardUID: string;
    currentUser: AuthUser.TModel;
    viewportRef: React.RefObject<HTMLDivElement | null>;
    isExpanded?: bool;
    setIsExpanded?: React.Dispatch<React.SetStateAction<bool>>;
    onClose?: () => void;
    onFlip?: (card: ProjectCard.TModel) => void;
    onEditModeStateChange?: (isEditing: bool, cancelEdit: (() => void) | null) => void;
}

const BoardCard = memo(
    ({
        projectUID,
        cardUID,
        currentUser,
        viewportRef,
        isExpanded = false,
        setIsExpanded,
        onClose,
        onFlip,
        onEditModeStateChange,
    }: IBoardCardProps): React.JSX.Element => {
        const { setPageAliasRef } = usePageHeader();
        const identity = `${currentUser.uid}:${projectUID}:${cardUID}`;
        const {
            data: cardData,
            isFetching,
            error,
            refetch,
        } = useGetCardDetails(
            {
                project_uid: projectUID,
                card_uid: cardUID,
                sections: CARD_BODY_SECTIONS,
            },
            {
                placeholderData: (previous, previousQuery) =>
                    previousQuery?.queryKey[0] === `get-card-details-${projectUID}-${cardUID}` &&
                    previousQuery.queryKey[1] === currentUser.uid &&
                    previousQuery.queryKey[2] === useAuthStore.getState().getSessionVersion()
                        ? previous
                        : undefined,
            }
        );
        const focusedCardRef = useRef("");
        useEffect(() => {
            if (cardData?.card) useCardFlipStore.getState().remove(currentUser.uid, projectUID, cardUID);
        }, [cardData?.card, currentUser.uid, projectUID, cardUID]);
        const [t] = useTranslation();
        const socket = useSocket();
        const queryClient = useQueryClient();
        const navigate = usePageNavigateRef();
        const { on: onCardDeletedHandlers } = useCardDeletedHandlers({
            projectUID,
            cardUID,
            callback: () => {
                closeCard(currentUser.uid, projectUID, cardUID);
                Toast.Add.error(t("project.errors.Card deleted."));
                navigate(ROUTES.BOARD.MAIN(projectUID), { replace: true });
            },
        });
        useEffect(() => {
            const listener = {
                topic: ESocketTopic.Board as const,
                topicId: projectUID,
                event: SocketEvents.SERVER.BOARD.CARD.DETAILS_CHANGED.replace("{uid}", cardUID),
                eventKey: `card-execution-receipt-${projectUID}-${cardUID}`,
                callback: (data: unknown) => {
                    if (data && typeof data === "object" && "execution_receipt_changed" in data && data.execution_receipt_changed === true) {
                        void queryClient.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${cardUID}`] });
                    }
                },
            };
            socket.on(listener);
            return () => socket.off(listener);
        }, [cardUID, projectUID, queryClient, socket]);

        const linkedResourceChangedHandler = useMemo(
            () =>
                useCardLinkedResourceChangedHandlers({
                    projectUID,
                    cardUID,
                    detail: true,
                    callback: () => queryClient.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${cardUID}`] }),
                }),
            [cardUID, projectUID, queryClient]
        );
        const activeWorkersChangedHandler = useMemo(
            () =>
                useBoardChecklistProgressChangedHandlers({
                    projectUID,
                    subscriberKey: cardUID,
                    callback: (event) => {
                        if (event.card_uid === cardUID) {
                            queryClient.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${cardUID}`] });
                        }
                    },
                }),
            [cardUID, projectUID, queryClient]
        );

        useSwitchSocketHandlers({
            socket,
            handlers: cardData?.card?.source_type === "project_wiki" ? [linkedResourceChangedHandler] : [activeWorkersChangedHandler],
            dependencies: [cardData?.card?.source_type, linkedResourceChangedHandler, activeWorkersChangedHandler],
        });

        useEffect(() => {
            if (!error) {
                return;
            }

            const { handle } = setupApiErrorHandler({
                [EHttpStatus.HTTP_403_FORBIDDEN]: {
                    after: () => {
                        closeCard(currentUser.uid, projectUID, cardUID);
                        navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true });
                    },
                },
                [EHttpStatus.HTTP_404_NOT_FOUND]: {
                    after: () => {
                        closeCard(currentUser.uid, projectUID, cardUID);
                        navigate(ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND), { replace: true });
                    },
                },
            });

            handle(error);
        }, [error]);

        useEffect(() => {
            const key = `${currentUser.uid}:${projectUID}:${cardUID}`;
            if (!cardData?.card || isFetching || focusedCardRef.current === key) {
                return;
            }
            focusedCardRef.current = key;
            focusCard(currentUser.uid, {
                projectUID,
                cardUID,
                title: cardData.card.linked_resource?.title || cardData.card.title,
            });
        }, [cardData, cardUID, currentUser.uid, isFetching, projectUID]);

        useEffect(() => {
            setPageAliasRef.current(cardData?.card?.linked_resource?.title || cardData?.card?.title || "");
            if (!cardData || isFetching) {
                return;
            }

            socket.subscribe(ESocketTopic.BoardCard, [cardUID], () => {
                onCardDeletedHandlers();
            });

            return () => {
                socket.unsubscribe(ESocketTopic.BoardCard, [cardUID]);
            };
        }, [cardData, cardUID]);

        return (
            <>
                {error && (
                    <Box role="alert" data-card-surface="" className="shrink-0 rounded-xl border bg-background p-4">
                        {!cardData ? (
                            <Dialog.Title className="text-base font-semibold">{t("card.Could not load card")}</Dialog.Title>
                        ) : (
                            <p className="text-sm text-muted-foreground">{t("card.Could not refresh card")}</p>
                        )}
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="mt-2"
                            disabled={isFetching}
                            onClick={() => {
                                if (!isFetching) void refetch();
                            }}
                        >
                            {t("common.Retry")}
                        </Button>
                    </Box>
                )}
                {!cardData && (
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className={cn("shrink-0 self-end", !error && "absolute right-4 top-4 z-[101]")}
                        onClick={onClose}
                    >
                        {t("common.Close")}
                    </Button>
                )}
                {!cardData ? (
                    !error && <SkeletonBoardCard isExpanded={isExpanded} />
                ) : (
                    <BoardCardProvider
                        key={identity}
                        projectUID={projectUID}
                        card={cardData.card}
                        currentUser={currentUser}
                        viewportRef={viewportRef}
                    >
                        <BoardCardResult
                            sectionLoading={!CARD_BODY_SECTIONS.every((section) => cardData.loaded_sections?.includes(section))}
                            whispersAllowed={cardData.whispers_allowed === true}
                            linkedWikis={cardData.linked_wikis}
                            isExpanded={isExpanded}
                            setIsExpanded={setIsExpanded}
                            onClose={onClose}
                            onFlip={onFlip}
                            onEditModeStateChange={onEditModeStateChange}
                        />
                    </BoardCardProvider>
                )}
            </>
        );
    }
);

export function SkeletonBoardCard({ isExpanded = false }: { isExpanded?: boolean }): React.JSX.Element {
    const [t] = useTranslation();
    return (
        <Flex
            direction="col"
            className="max-md:pb-[env(safe-area-inset-bottom)] max-md:pt-[env(safe-area-inset-top)] h-full min-h-0 gap-2"
            data-card-loading=""
        >
            <Dialog.Title role="status" className="sr-only">
                {t("common.Loading...")}
            </Dialog.Title>
            <Box
                data-card-surface=""
                aria-hidden="true"
                className={cn(
                    "relative min-h-0 min-w-0 max-w-full flex-1 overflow-hidden border bg-background px-4 py-4 sm:px-6 sm:py-6",
                    isExpanded ? "border-0 shadow-none" : "rounded-2xl shadow-2xl"
                )}
            >
                <Flex direction="col" className="mb-3 shrink-0 gap-2 border-b-2 pb-3">
                    <Skeleton className="h-7 w-3/5 max-w-96" />
                    <Flex gap="2" wrap>
                        <Skeleton className="h-5 w-16" />
                        <Skeleton className="h-5 w-24" />
                    </Flex>
                    <Dialog.Description asChild>
                        <Skeleton className="h-3 w-40 max-w-full" />
                    </Dialog.Description>
                </Flex>
                <Flex direction="col" gap="4" className="min-w-0 py-6 pr-1">
                    <Box className="space-y-3 rounded-md border p-3">
                        <Skeleton className="h-4 w-1/3" />
                        <Flex gap="2" wrap>
                            <Skeleton className="h-5 w-20" />
                            <Skeleton className="h-5 w-24" />
                        </Flex>
                    </Box>
                    <Skeleton className="h-4 w-3/4 max-w-96" />
                    <Skeleton className="h-4 w-1/2" />
                    <Skeleton className="h-4 w-2/5" />
                    <Skeleton className="h-4 w-1/2" />
                    <Skeleton className="h-3 w-5/6" />
                    <Skeleton className="mt-2 h-8 w-full" />
                </Flex>
            </Box>
            <Flex justify="center" aria-hidden="true" className="shrink-0">
                <Box className="flex w-full max-w-full gap-1 rounded-2xl border bg-background p-1 shadow-lg md:w-auto md:rounded-full">
                    <Skeleton className="h-10 w-20 flex-1 rounded-xl md:flex-none md:rounded-full" />
                    <Skeleton className="h-10 w-20 flex-1 rounded-xl md:flex-none md:rounded-full" />
                </Box>
            </Flex>
        </Flex>
    );
}

interface IBoardCardResultProps {
    sectionLoading: boolean;
    whispersAllowed?: boolean;
    linkedWikis?: IGetCardDetailsResponse["linked_wikis"];
    isExpanded: bool;
    setIsExpanded?: React.Dispatch<React.SetStateAction<bool>>;
    onClose?: () => void;
    onFlip?: (card: ProjectCard.TModel) => void;
    onEditModeStateChange?: (isEditing: bool, cancelEdit: (() => void) | null) => void;
}

function BoardCardResult(props: IBoardCardResultProps): React.JSX.Element {
    const { card } = useBoardCard();

    if (card.source_type === "project_card") {
        return <BoardCardMirror {...props} />;
    }

    if (card.source_type === "project_wiki" && card.linked_resource) {
        return <BoardLinkedWikiCard {...props} />;
    }

    return <BoardTaskCardResult {...props} />;
}

function BoardTaskCardResult(props: IBoardCardResultProps): React.JSX.Element {
    const { projectUID, card, currentUser } = useBoardCard();
    const revision = card.useField("last_change_seq");
    return (
        <DecisionProvider projectUID={projectUID} cardUID={card.uid} userUID={currentUser.uid} sourceRevision={revision}>
            <BoardTaskCardContent {...props} />
        </DecisionProvider>
    );
}

function BoardTaskCardContent({
    sectionLoading,
    isExpanded,
    setIsExpanded,
    onClose,
    onFlip,
    onEditModeStateChange,
    whispersAllowed = false,
    linkedWikis = [],
}: IBoardCardResultProps): React.JSX.Element {
    const { card, projectUID, currentUser, canEditCard, isCardEditing, leaveCardEditMode } = useBoardCard();
    const { isActionPanelOpen, setIsCommentPanelOpen, commentLayoutMode, commentSurfaceRef } = useBoardCardPanel();
    const { boardChat } = useBoardController();
    const { cancelSections } = useBoardCardSectionSaveActions();
    const [t] = useTranslation();
    const attachments = ProjectCardAttachment.Model.useModels((model) => model.card_uid === card.uid, [card]);
    const checklists = ProjectChecklist.Model.useModels((model) => model.card_uid === card.uid, [card]);
    const title = card.useField("title");
    const description = card.useField("description");
    const visibility = card.useField("visibility");
    const canChangeVisibility = card.useField("can_change_visibility");
    const visibilityMembers = card.useForeignFieldArray("project_members");
    const deadlineAt = card.useField("deadline_at");
    const isCheckCard = card.useField("is_check_card") ?? false;
    const completed = card.useField("completed") ?? false;
    const { mutateAsync: setCardCompletedAsync, isPending: isCompletionPending } = useSetCardCompleted({ interceptToast: true });
    const toggleCompleted = useCallback(() => {
        const nextCompleted = !(card.completed ?? false);
        setCardCompletedAsync({ project_uid: projectUID, card_uid: card.uid, completed: nextCompleted }).catch(() => {
            // The shared mutation restores the model and reports the API error.
        });
    }, [card, completed, projectUID, setCardCompletedAsync]);
    // Check-card view: no body and no user checklist. Comments, members, and deadlines never affect it.
    const isCheckCardView = useMemo(() => {
        const content = typeof description?.content === "string" ? description.content : "";
        return !content.trim() && checklists.length === 0;
    }, [description, checklists.length]);
    const hasRunningBot = useHasRunningBot({ type: "card", targetUID: card.uid });
    const priority = card.useField("priority") ?? "P2";
    const archivedAt = card.useField("archived_at");
    const workState = card.useField("work_state");
    const checklistCompleted = card.useField("checklist_completed_count") ?? 0;
    const checklistTotal = card.useField("checklist_total_count") ?? 0;
    const motionVisibility = useCardMotionVisibility();
    const [isPriorityOpen, setIsPriorityOpen] = useState(false);
    const { deadlineClock } = useBoard();
    const visualSignals = getCardVisualSignals({
        priority,
        deadlineAt,
        archivedAt,
        workState,
        completed,
        checklist: { completed: checklistCompleted, total: checklistTotal },
        agentRunning: hasRunningBot,
        now: deadlineClock,
        ...motionVisibility,
        visible: motionVisibility.visible && !isPriorityOpen,
    });
    const setMotionSurfaceRef = useCallback(
        (node: HTMLDivElement | null) => {
            commentSurfaceRef.current = node;
            motionVisibility.ref(node);
        },
        [commentSurfaceRef, motionVisibility.ref]
    );
    const contentViewportRef = useRef<HTMLDivElement | null>(null);

    type NavigationIntent = { cardUID: string; section: string; blockUID?: string; commentUID?: string; unread?: boolean };
    const [navigationIntent, setNavigationIntent] = useState<NavigationIntent | null>(null);
    const navigateUnread = useCallback(
        (section: "comments" | "description", commentUID?: string) => {
            setNavigationIntent({ cardUID: card.uid, section, commentUID, unread: true });
        },
        [card]
    );
    useUnreadChangeNavigation(navigateUnread);
    useEffect(() => {
        const openOutlineSection = (event: Event) => {
            const intent = (event as CustomEvent<NavigationIntent>).detail;
            if (intent.cardUID !== card.uid) return;
            setNavigationIntent(intent);
        };
        window.addEventListener(WORKBENCH_OUTLINE_EVENT, openOutlineSection);
        return () => window.removeEventListener(WORKBENCH_OUTLINE_EVENT, openOutlineSection);
    }, [card]);
    useEffect(() => {
        if (!navigationIntent || navigationIntent.cardUID !== card.uid || sectionLoading) return;
        if (navigationIntent.section === "comments") setIsCommentPanelOpen(true);
        const surface = commentSurfaceRef.current;
        if (!surface) return;
        const focusTarget = () => {
            const candidates = navigationIntent.commentUID
                ? surface.querySelectorAll<HTMLElement>("[data-card-comment-uid]")
                : navigationIntent.blockUID
                  ? surface.querySelectorAll<HTMLElement>("[data-card-block-uid]")
                  : surface.querySelectorAll<HTMLElement>("[data-card-outline-section]");
            const target = [...candidates].find(
                (element) =>
                    element.getBoundingClientRect().width > 0 &&
                    (navigationIntent.commentUID
                        ? element.dataset.cardCommentUid === navigationIntent.commentUID
                        : navigationIntent.blockUID
                          ? element.dataset.cardBlockUid === navigationIntent.blockUID
                          : element.dataset.cardOutlineSection === navigationIntent.section)
            );
            if (!target) return;
            const viewport = target.closest<HTMLElement>("[data-card-content-viewport]");
            if (viewport) viewport.scrollTop += target.getBoundingClientRect().top - viewport.getBoundingClientRect().top;
            else target.scrollIntoView({ behavior: "auto", block: "start" });
            target.focus({ preventScroll: true });
            if (navigationIntent.unread) highlightUnreadTarget(target);
            setNavigationIntent(null);
        };
        const observer = new MutationObserver(focusTarget);
        observer.observe(surface, { childList: true, subtree: true });
        const frame = requestAnimationFrame(focusTarget);
        const timeout = window.setTimeout(() => setNavigationIntent(null), 4000);
        return () => {
            observer.disconnect();
            cancelAnimationFrame(frame);
            window.clearTimeout(timeout);
        };
    }, [navigationIntent, card, sectionLoading, setIsCommentPanelOpen, commentSurfaceRef]);

    useEffect(() => {
        return () => {
            getEditorStore().setCurrentEditor(null);
        };
    }, []);

    useEffect(() => {
        const cancelCardEdit = () => {
            try {
                cancelSections();
            } finally {
                leaveCardEditMode();
            }
        };

        onEditModeStateChange?.(isCardEditing, isCardEditing ? cancelCardEdit : null);
    }, [cancelSections, isCardEditing, leaveCardEditMode, onEditModeStateChange]);

    useEffect(() => {
        return () => {
            onEditModeStateChange?.(false, null);
        };
    }, [onEditModeStateChange]);

    return (
        <>
            {hasRunningBot && (
                <ShineBorder
                    className="z-[999999]"
                    style={visualSignals.motion === "agent" ? undefined : { animation: "none", willChange: "auto" }}
                />
            )}
            <Flex direction="col" className="max-md:pb-[env(safe-area-inset-bottom)] max-md:pt-[env(safe-area-inset-top)] h-full min-h-0 gap-2">
                <Flex className={cn("min-h-0 min-w-0 flex-1", isExpanded ? "overflow-hidden" : "overflow-visible")}>
                    <Box
                        ref={setMotionSurfaceRef}
                        data-card-surface=""
                        data-priority={priority}
                        data-card-motion={visualSignals.motion}
                        data-card-privacy={cardVisibilityPresentation(
                            visibility,
                            visibilityMembers.some((member) => member.isValidUser() && member.membership_classification === "external")
                        )}
                        className={cn(
                            "relative min-h-0 min-w-0 max-w-full flex-1 border bg-background px-4 py-4 sm:px-6 sm:py-6",
                            visualSignals.motion === "overdue" && "board-card-overdue-shake",
                            (visualSignals.motion === "critical" || visualSignals.motion === "emergency") && "board-card-priority-pulse",
                            isExpanded ? "overflow-hidden border-0 shadow-none" : "overflow-visible rounded-2xl shadow-2xl"
                        )}
                    >
                        {!isExpanded && (
                            <Box className="hidden sm:block">
                                <BoardCardActionRelationship buttonClassName="" />
                            </Box>
                        )}
                        <Box className="relative flex h-full min-h-0 min-w-0 flex-col overflow-visible">
                            <Dialog.Header className="sticky top-0 z-[100] mb-3 shrink-0 border-b-2 bg-background pb-3 text-left sm:-top-2">
                                <Flex items="center" gap="2" className="min-w-0">
                                    {deadlineAt && isCheckCard && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="shrink-0"
                                            aria-label={t(completed ? "card.Mark as not done" : "card.Mark as done")}
                                            title={t(completed ? "card.Mark as not done" : "card.Mark as done")}
                                            disabled={isCompletionPending}
                                            aria-pressed={completed}
                                            onClick={toggleCompleted}
                                        >
                                            <IconComponent icon={completed ? "check" : "circle"} size="5" />
                                        </Button>
                                    )}
                                    <BoardCardTitle key={`board-card-title-${card.uid}`} className={isExpanded ? "mr-28 sm:mr-52" : "mr-28"} />
                                </Flex>
                                <BoardCardPriority onOpenChange={setIsPriorityOpen} effectsEnabled={!visualSignals.suppressed} />
                                <Flex gap="3" wrap items="center" className="min-w-0">
                                    <CardTypeBadges card={card} showVisibility={visibility === "PRIVATE" || !(canEditCard && canChangeVisibility)} />
                                    <BoardCardVisibility />
                                    {!canEditCard && (
                                        <Box textSize="xs" className="text-muted-foreground">
                                            {t("card.Card editing restricted by board role")}
                                        </Box>
                                    )}
                                    {isExpanded ? (
                                        <Box textSize="sm" className="text-muted">
                                            <BoardCardColumnName key={`board-card-column-name-${card.uid}`} />
                                        </Box>
                                    ) : (
                                        <Dialog.Description>
                                            <BoardCardColumnName key={`board-card-column-name-${card.uid}`} />
                                        </Dialog.Description>
                                    )}
                                    <BoardCardLabelList key={`board-card-label-list-${card.uid}`} />
                                </Flex>
                                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                                    <CardTimestamps card={card} />
                                    <CardNextAction card={card} editable />
                                </div>
                                <Flex items="start" gap="1" className="absolute right-0 top-0 !mt-0 pl-3">
                                    {isExpanded && (
                                        <Box className="hidden sm:flex sm:items-center sm:gap-1">
                                            <BoardCardActionRelationship buttonClassName="" isExpanded />
                                        </Box>
                                    )}
                                    {!!setIsExpanded && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="size-8"
                                            title={t(isExpanded ? "common.Collapse" : "common.Expand")}
                                            aria-label={t(isExpanded ? "common.Collapse" : "common.Expand")}
                                            onClick={() => setIsExpanded((value) => !value)}
                                        >
                                            <IconComponent icon={isExpanded ? "minimize-2" : "maximize-2"} size="4" />
                                        </Button>
                                    )}
                                    {!!onFlip && (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="size-8"
                                            title={t("card.Flip card")}
                                            aria-label={t("card.Flip card")}
                                            onClick={() => onFlip(card)}
                                        >
                                            <IconComponent icon="layers" size="4" />
                                        </Button>
                                    )}
                                    {isExpanded ? (
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="icon"
                                            className="size-8"
                                            title={t("common.Close")}
                                            aria-label={t("common.Close")}
                                            onClick={onClose}
                                        >
                                            <IconComponent icon="x" size="4" />
                                        </Button>
                                    ) : (
                                        <Dialog.CloseButton className="inline-flex size-8 items-center justify-center" />
                                    )}
                                </Flex>
                            </Dialog.Header>
                            <Flex gap="3" direction={commentLayoutMode === "panel" ? "row" : "col"} className="min-h-0 flex-1">
                                <Box data-card-content-frame="" className="relative min-h-0 min-w-0 flex-1 pl-10">
                                    <Box
                                        ref={contentViewportRef}
                                        data-card-content-viewport=""
                                        role="region"
                                        aria-busy={sectionLoading}
                                        aria-label={title}
                                        tabIndex={0}
                                        className="h-full min-h-0 overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
                                    >
                                        <Flex direction="col" gap="4" className="min-w-0 py-6 pr-1">
                                            <BoardCardMobileActions />
                                            {sectionLoading && <p role="status">{t("common.Loading...")}</p>}
                                            {!sectionLoading && (
                                                <>
                                                    <CardColumnHistory card={card} />
                                                    <BoardCardDecisionGroup />
                                                    {isCheckCardView ? (
                                                        <BoardCardCheckBody
                                                            key={`board-card-check-body-${card.uid}`}
                                                            scrollParentRef={contentViewportRef}
                                                        />
                                                    ) : (
                                                        <>
                                                            <Flex direction={{ initial: "col", sm: "row" }} gap="4">
                                                                {visibility !== "PRIVATE" && (
                                                                    <BoardCardSection
                                                                        title="card.Members"
                                                                        className="sm:w-1/2"
                                                                        contentClassName="flex gap-1"
                                                                    >
                                                                        <BoardCardMemberList key={`board-card-member-list-${card.uid}`} />
                                                                    </BoardCardSection>
                                                                )}
                                                                <BoardCardSection title="card.Deadline" className="sm:w-1/2">
                                                                    <BoardCardDeadline key={`board-card-deadline-${card.uid}`} />
                                                                </BoardCardSection>
                                                            </Flex>
                                                            <BoardTaskMetadataSection cardUID={card.uid} />
                                                            {linkedWikis.length > 0 && (
                                                                <BoardCardSection title="wiki.Linked wiki">
                                                                    <ul className="space-y-1 text-sm">
                                                                        {linkedWikis.map((wiki) => (
                                                                            <li key={wiki.wiki_uid}>
                                                                                <a
                                                                                    className="text-primary hover:underline"
                                                                                    href={ROUTES.BOARD.WIKI_PAGE(projectUID, wiki.wiki_uid)}
                                                                                >
                                                                                    {wiki.title}
                                                                                </a>
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                </BoardCardSection>
                                                            )}
                                                            <BoardCardSection
                                                                title="card.Description"
                                                                className="relative min-h-56"
                                                                data-card-outline-section="description"
                                                                tabIndex={-1}
                                                            >
                                                                <BoardCardDescription
                                                                    key={`board-card-description-${card.uid}`}
                                                                    scrollParentRef={contentViewportRef}
                                                                />
                                                            </BoardCardSection>
                                                            <CardAppArchiveReceipts
                                                                projectUID={projectUID}
                                                                cardUID={card.uid}
                                                                userUID={currentUser.uid}
                                                            />
                                                            {checklists.length > 0 && (
                                                                <BoardCardSection
                                                                    title="card.Checklists"
                                                                    data-card-outline-section="checklists"
                                                                    tabIndex={-1}
                                                                >
                                                                    <BoardCardChecklistGroup key={`board-card-checklist-${card.uid}`} />
                                                                </BoardCardSection>
                                                            )}
                                                            {attachments.length > 0 && (
                                                                <BoardCardSection
                                                                    title="card.Attached files"
                                                                    data-card-outline-section="attachments"
                                                                    tabIndex={-1}
                                                                >
                                                                    <BoardCardAttachmentList key={`board-card-attachment-list-${card.uid}`} />
                                                                </BoardCardSection>
                                                            )}
                                                        </>
                                                    )}
                                                    {whispersAllowed && visibility !== "PRIVATE" && <BoardCardWhispers />}
                                                    <BoardCardMobileComments scrollableRef={contentViewportRef} />
                                                </>
                                            )}
                                        </Flex>
                                    </Box>
                                </Box>
                                <BoardCardCommentPanel enabled />
                                {visibility !== "PRIVATE" && !!boardChat && <BoardCardExpandedChatScope isExpanded={isExpanded} />}
                                <Box
                                    w="full"
                                    maxW={{ sm: "40" }}
                                    className={cn("shrink-0", (commentLayoutMode !== "panel" || !isActionPanelOpen) && "hidden")}
                                >
                                    <BoardCardSection title="card.Actions" titleClassName="mb-2">
                                        <BoardCardActionList key={`board-card-action-list-${card.uid}`} />
                                    </BoardCardSection>
                                </Box>
                            </Flex>
                        </Box>
                    </Box>
                </Flex>
                <BoardCardFloatingNav isExpanded={isExpanded} sectionLoading={sectionLoading} />
            </Flex>
        </>
    );
}

function BoardCardMobileActions(): React.JSX.Element | null {
    const { card } = useBoardCard();
    const { isActionPanelOpen, commentLayoutMode } = useBoardCardPanel();

    if (!isActionPanelOpen || commentLayoutMode === "panel") {
        return null;
    }

    return (
        <Box>
            <BoardCardSection title="card.Actions" titleClassName="mb-2">
                <BoardCardActionList key={`board-card-action-list-mobile-${card.uid}`} />
            </BoardCardSection>
        </Box>
    );
}

function BoardCardMobileComments({ scrollableRef }: { scrollableRef?: React.RefObject<HTMLDivElement | null> }): React.JSX.Element | null {
    const { card } = useBoardCard();
    const { isCommentPanelOpen, commentLayoutMode } = useBoardCardPanel();

    if (!isCommentPanelOpen || commentLayoutMode !== "mobile") {
        return null;
    }

    return (
        <Box data-card-outline-section="comments" tabIndex={-1}>
            <BoardCardSection title="card.Comments">
                <BoardCommentList key={`board-card-comment-list-mobile-${card.uid}`} scrollableRef={scrollableRef} />
            </BoardCardSection>
        </Box>
    );
}

function BoardCardCommentPanel({ enabled }: { enabled: boolean }): React.JSX.Element {
    const { card } = useBoardCard();
    const { isCommentPanelOpen, commentLayoutMode } = useBoardCardPanel();
    const commentViewportRef = useRef<HTMLDivElement | null>(null);
    const [t] = useTranslation();
    const isPanelLayout = commentLayoutMode === "panel";
    const showComposer = enabled && isCommentPanelOpen;
    const isOpen = showComposer && isPanelLayout;
    const panelRef = useRef<HTMLDivElement | null>(null);
    const dragRef = useRef<{ pointerId: number; startX: number; startWidth: number } | null>(null);
    const [width, setWidth] = useState(readCommentPanelWidth);
    const widthRef = useRef(width);
    const [maxWidth, setMaxWidth] = useState(MAX_COMMENT_PANEL_WIDTH);
    const [isResizing, setIsResizing] = useState(false);

    const sharedWidth = useCallback(() => {
        const panel = panelRef.current;
        const body = panel?.previousElementSibling;
        return (body?.getBoundingClientRect().width ?? 0) + (panel?.getBoundingClientRect().width ?? 0);
    }, []);
    const applyWidth = useCallback(
        (nextWidth: number) => {
            const available = sharedWidth();
            const bounds = getCommentPanelWidthBounds(available);
            const next = clampCommentPanelWidth(nextWidth, available);
            widthRef.current = next;
            setWidth(next);
            setMaxWidth(bounds.max);
        },
        [sharedWidth]
    );

    useEffect(() => {
        if (!isPanelLayout || !panelRef.current?.parentElement) {
            return;
        }
        const observer = new ResizeObserver(() => applyWidth(widthRef.current));
        observer.observe(panelRef.current.parentElement);
        applyWidth(widthRef.current);
        return () => observer.disconnect();
    }, [applyWidth, isPanelLayout]);

    useEffect(() => {
        return () => {
            document.documentElement.style.userSelect = "";
            document.documentElement.style.cursor = "";
        };
    }, []);

    const stopResizing = (event: React.PointerEvent<HTMLDivElement>) => {
        if (dragRef.current?.pointerId !== event.pointerId) {
            return;
        }
        dragRef.current = null;
        setIsResizing(false);
        document.documentElement.style.userSelect = "";
        document.documentElement.style.cursor = "";
        saveCommentPanelWidth(widthRef.current);
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
        }
    };

    const startResizing = (event: React.PointerEvent<HTMLDivElement>) => {
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startWidth: widthRef.current };
        setIsResizing(true);
        document.documentElement.style.userSelect = "none";
        document.documentElement.style.cursor = "col-resize";
    };

    const resizeWithKeyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
        const available = sharedWidth();
        const bounds = getCommentPanelWidthBounds(available);
        const next =
            event.key === "ArrowLeft"
                ? widthRef.current + 20
                : event.key === "ArrowRight"
                  ? widthRef.current - 20
                  : event.key === "Home"
                    ? bounds.min
                    : event.key === "End"
                      ? bounds.max
                      : null;
        if (next === null) {
            return;
        }
        event.preventDefault();
        applyWidth(next);
        saveCommentPanelWidth(widthRef.current);
    };

    return (
        <Box
            ref={panelRef}
            data-card-outline-section="comments"
            tabIndex={-1}
            className={cn(
                "relative min-h-0 shrink-0 overflow-hidden",
                !isPanelLayout && "w-full",
                isResizing ? "transition-none" : "transition-[width,min-width] duration-300 motion-reduce:transition-none"
            )}
            style={isPanelLayout ? { width: isOpen ? width : 0, minWidth: isOpen ? width : 0 } : undefined}
            aria-hidden={!showComposer}
        >
            {isOpen && (
                <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label={t("card.Comments panel width")}
                    aria-valuemin={MIN_COMMENT_PANEL_WIDTH}
                    aria-valuemax={maxWidth}
                    aria-valuenow={width}
                    tabIndex={0}
                    className={cn(
                        "absolute inset-y-0 left-0 z-10 w-2 cursor-col-resize touch-none",
                        "before:absolute before:inset-y-0 before:left-0 before:w-px before:bg-border hover:before:bg-primary",
                        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                    )}
                    onPointerDown={startResizing}
                    onPointerMove={(event) => {
                        const drag = dragRef.current;
                        if (drag?.pointerId === event.pointerId) {
                            applyWidth(drag.startWidth + drag.startX - event.clientX);
                        }
                    }}
                    onPointerUp={stopResizing}
                    onPointerCancel={stopResizing}
                    onLostPointerCapture={stopResizing}
                    onKeyDown={resizeWithKeyboard}
                    onDoubleClick={() => {
                        applyWidth(DEFAULT_COMMENT_PANEL_WIDTH);
                        saveCommentPanelWidth(widthRef.current);
                    }}
                />
            )}
            <Box
                className={cn(
                    isPanelLayout && "h-full",
                    "overflow-hidden bg-background transition-opacity duration-200 motion-reduce:transition-none",
                    showComposer ? "opacity-100" : "pointer-events-none h-0 opacity-0"
                )}
            >
                <Flex direction="col" className="h-full min-h-0">
                    {isPanelLayout && <Box>{t("card.Comments")}</Box>}
                    <Box className={isPanelLayout ? "px-4 py-3" : undefined}>
                        {showComposer && <BoardCommentForm variant={isPanelLayout ? "panel" : "mobile"} />}
                    </Box>
                    <ScrollArea.Root className="min-h-0 flex-1" viewportRef={commentViewportRef}>
                        {isOpen && (
                            <BoardCommentList key={`board-card-comment-list-${card.uid}`} scrollableRef={commentViewportRef} className="px-4 py-3" />
                        )}
                    </ScrollArea.Root>
                </Flex>
            </Box>
        </Box>
    );
}

function BoardCardExpandedChatScope({ isExpanded }: { isExpanded: bool }): null {
    const { card } = useBoardCard();
    const { bot, projectUID, setLockedScope } = useBoardChat();
    const canUseChat = !!projectUID && !!bot?.uid;

    useEffect(() => {
        if (!isExpanded || !canUseChat) {
            return;
        }

        const cardUID = card.uid;

        setLockedScope(["card", cardUID]);

        return () => {
            setLockedScope((value) => {
                if (!value || value[0] !== "card" || value[1] !== cardUID) {
                    return value;
                }

                return undefined;
            });
        };
    }, [canUseChat, card, isExpanded]);

    return null;
}

export function BoardCardFloatingNav({ isExpanded, sectionLoading }: { isExpanded: bool; sectionLoading: boolean }): React.JSX.Element {
    const { projectUID, card, currentUser } = useBoardCard();
    const { isCommentPanelOpen, toggleCommentPanel, isActionPanelOpen, toggleActionPanel } = useBoardCardPanel();
    const { canEditCard, isCardEditing, enterCardEditMode, leaveCardEditMode, getCardEditSession } = useBoardCard();
    const { cancelSections, saveSections, getSectionDraftSnapshot } = useBoardCardSectionSaveActions();
    const [t] = useTranslation();
    const [isSaving, setIsSaving] = useState(false);
    const { mutateAsync: changeCardDetailsMutateAsync } = useChangeCardDetails({ interceptToast: true });
    const { mutateAsync: replaceContentBlocksAsync } = useReplaceCardContentBlocks({ interceptToast: true });

    const handleSaveEditing = useCallback(async () => {
        if (isSaving || sectionLoading) {
            return;
        }

        const session = getCardEditSession();
        const draftSnapshot = getSectionDraftSnapshot();
        setIsSaving(true);

        try {
            const details = await saveSections();
            if (!details) {
                Toast.Add.error(t("card.unsavedChanges.Keep editing"));
                return;
            }

            if (Object.keys(details).length > 0) {
                const promise = changeCardDetailsMutateAsync({
                    project_uid: projectUID,
                    card_uid: card.uid,
                    ...details,
                });

                Toast.Add.promise(promise, {
                    loading: t("common.Changing..."),
                    error: (error) => {
                        const messageRef = { message: "" };
                        const { handle } = setupApiErrorHandler({}, messageRef);

                        handle(error);
                        return messageRef.message;
                    },
                    success: () => t("successes.Card changed successfully."),
                });

                try {
                    await promise;
                } catch {
                    return;
                }
            }

            if (details && details.description) {
                const blocks = extractContentBlocks(details.description.content ?? "");
                if (blocks.length >= 0) {
                    try {
                        await replaceContentBlocksAsync({
                            project_uid: projectUID,
                            card_uid: card.uid,
                            blocks,
                        });
                    } catch {
                        // 블록 동기화 실패는 본문 저장를 롤백하지 않는다 — 다음 저장 시 재동기화
                    }
                }
            }

            // A completed request owns only the draft and edit session it submitted.
            if (getSectionDraftSnapshot() === draftSnapshot) {
                leaveCardEditMode(session);
            }
        } finally {
            setIsSaving(false);
        }
    }, [
        card,
        changeCardDetailsMutateAsync,
        isSaving,
        leaveCardEditMode,
        projectUID,
        replaceContentBlocksAsync,
        saveSections,
        sectionLoading,
        getCardEditSession,
        getSectionDraftSnapshot,
    ]);

    const handleCancelEditing = useCallback(() => {
        try {
            cancelSections();
        } finally {
            leaveCardEditMode();
        }
    }, [cancelSections, leaveCardEditMode]);

    return (
        <>
            <Floating.Nav
                trailing={
                    <CardFlipTray
                        userUID={currentUser.uid}
                        projectUID={projectUID}
                        currentCard={{ uid: card.uid, title: card.title }}
                        disabled={isCardEditing}
                    />
                }
                className={cn("z-[110]", !isExpanded && "mx-auto max-w-[100vw] sm:max-w-[90vw] lg:max-w-[1120px]")}
                contentClassName="bg-background"
                itemClassName="h-10 px-3"
                labelClassName="sr-only md:not-sr-only md:inline"
                items={[
                    ...(!canEditCard
                        ? []
                        : !isCardEditing
                          ? [
                                {
                                    key: "edit",
                                    label: t("common.Edit"),
                                    icon: "pen",
                                    onClick: enterCardEditMode,
                                },
                            ]
                          : [
                                {
                                    key: "cancel",
                                    label: t("common.Cancel"),
                                    icon: "x",
                                    variant: "outline" as const,
                                    onClick: handleCancelEditing,
                                },
                                {
                                    key: "save",
                                    label: t("common.Save"),
                                    icon: "save",
                                    variant: "default" as const,
                                    disabled: isSaving || sectionLoading,
                                    onClick: handleSaveEditing,
                                },
                            ]),
                    {
                        key: "actions",
                        label: t("card.Actions"),
                        icon: "list",
                        active: isActionPanelOpen,
                        onClick: toggleActionPanel,
                    },
                    {
                        key: "comments",
                        label: t("card.Comments"),
                        icon: "message-square",
                        active: isCommentPanelOpen,
                        onClick: () => {
                            toggleCommentPanel();
                            if (!isCommentPanelOpen) {
                                window.dispatchEvent(
                                    new CustomEvent(WORKBENCH_OUTLINE_EVENT, { detail: { cardUID: card.uid, section: "comments" } })
                                );
                            }
                        },
                    },
                ]}
            />
        </>
    );
}

interface IBoardCardSectionProps extends React.HTMLAttributes<HTMLDivElement> {
    title: string;
    titleClassName?: string;
    contentClassName?: string;
}

const BoardCardSection = forwardRef<HTMLDivElement, IBoardCardSectionProps>(
    ({ title, titleClassName, contentClassName, children, ...props }, ref) => {
        const [t] = useTranslation();
        return (
            <Box {...props} ref={ref}>
                <Box mb="1" className={titleClassName}>
                    {t(title)}
                </Box>
                <Box className={contentClassName}>{children}</Box>
            </Box>
        );
    }
);

export default BoardCard;
