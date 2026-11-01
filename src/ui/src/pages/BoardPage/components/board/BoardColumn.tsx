"use client";

import WikiCardDrop from "./WikiCardDrop";
import CrossBoardDrop from "./crossBoard/CrossBoardDrop";
import { useTranslation } from "react-i18next";
import { usePageLocation } from "@/core/hooks/usePageLocation";

import useColumnCardSort from "./useColumnCardSort";
import { sortColumnCards } from "./columnCardSort";
import { memo, type RefObject, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import invariant from "tiny-invariant";
import BoardColumnCard, { BoardColumnCardShadow, SkeletonBoardColumnCard } from "@/pages/BoardPage/components/board/BoardColumnCard";
import { useBoard } from "@/core/providers/BoardProvider";
import { ProjectCard, ProjectColumn } from "@/core/models";
import { BoardAddCardProvider } from "@/core/providers/BoardAddCardProvider";
import Box from "@/components/base/Box";
import Card from "@/components/base/Card";
import Flex from "@/components/base/Flex";
import ScrollArea from "@/components/base/ScrollArea";
import ShineBorder from "@/components/base/ShineBorder";
import Skeleton from "@/components/base/Skeleton";
import BoardColumnHeader from "@/pages/BoardPage/components/board/BoardColumnHeader";
import { cn } from "@/core/utils/ComponentUtils";
import BoardColumnAddCard from "@/pages/BoardPage/components/board/BoardColumnAddCard";
import BoardColumnAddCardButton from "@/pages/BoardPage/components/board/BoardColumnAddCardButton";
import useBoardCardCreatedHandlers from "@/controllers/socket/board/useBoardCardCreatedHandlers";
import useBoardUIColumnDeletedHandlers from "@/controllers/socket/board/column/useBoardUIColumnDeletedHandlers";
import { Utils } from "@langboard/core/utils";
import { columnRowDndHelpers } from "@/core/helpers/dnd";
import { TColumnState } from "@/core/helpers/dnd/types";
import {
    BLOCK_BOARD_PANNING_ATTR,
    BOARD_CARD_FOCUS_EVENT,
    BOARD_CARD_LOCATION_EVENT,
    BOARD_CARD_TOUCH_DND_ATTR,
    BOARD_COLUMN_MAX_HEIGHT_CLASS_NAMES,
    BOARD_COLUMN_TOUCH_DND_ATTR,
    BOARD_DND_SETTINGS,
    BOARD_DND_SYMBOL_SET,
    IBoardCardFocusEventDetail,
    IBoardCardLocationEventDetail,
} from "@/pages/BoardPage/components/board/BoardConstants";
import { COLUMN_IDLE } from "@/core/helpers/dnd/createDndColumnEvents";
import useRowReordered from "@/core/hooks/useRowReordered";
import { useHasRunningBot } from "@/core/stores/BotStatusStore";
import { useAuth } from "@/core/providers/AuthProvider";
import { getUserSettingsStore, useUserSettings } from "@/core/stores/UserSettingsStore";
import {
    boardColumnCardAncestorUIDs,
    indexBoardColumnCardHierarchy,
    visibleBoardColumnCardHierarchy,
    toggleChildCardCollapsed,
    estimateBoardColumnCardGroupHeight,
    buildBoardColumnCardHierarchy,
} from "@/pages/BoardPage/components/board/BoardColumnCardHierarchy";

export function SkeletonBoardColumn({ cardCount }: { cardCount: number }) {
    return (
        <Card.Root
            className={cn(
                BOARD_COLUMN_MAX_HEIGHT_CLASS_NAMES,
                "my-1 grid w-80 flex-shrink-0 grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden border-transparent"
            )}
        >
            <Card.Header className="flex flex-row items-start space-y-0 pb-1 pt-4 text-left font-semibold">
                <Skeleton h="6" className="w-1/3" />
            </Card.Header>
            <Card.Content className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden p-3">
                <Box pb="2.5" className="overflow-hidden">
                    <Flex direction="col" gap="3">
                        {Array.from({ length: cardCount }).map((_, index) => (
                            <SkeletonBoardColumnCard key={index} />
                        ))}
                    </Flex>
                </Box>
            </Card.Content>
        </Card.Root>
    );
}

const stateStyles: { [Key in TColumnState["type"]]: string } = {
    idle: "cursor-grab",
    "is-row-over": "ring-2 ring-primary",
    "is-dragging": "opacity-40 ring-2 ring-primary",
    "is-column-over": "bg-secondary",
};

export interface IBoardColumnProps {
    column: ProjectColumn.TModel;
    updateBoard: () => void;
}

function BoardColumn({ column, updateBoard, isDefaultCardColumn }: IBoardColumnProps & { isDefaultCardColumn: boolean }) {
    const { canDragAndDrop } = useBoard();
    const { mode } = useColumnCardSort(column.project_uid, column.uid);
    const scrollableRef = useRef<HTMLDivElement | null>(null);
    const outerFullHeightRef = useRef<HTMLDivElement | null>(null);
    const headerRef = useRef<HTMLDivElement | null>(null);
    const innerRef = useRef<HTMLDivElement | null>(null);
    const [state, setState] = useState<TColumnState>(COLUMN_IDLE);
    const [cardCount, setCardCount] = useState(0);
    const order = column.useField("order");
    const hasRunningBot = useHasRunningBot({ type: "project_column", targetUID: column.uid });

    useEffect(() => {
        const outer = outerFullHeightRef.current;
        const scrollable = scrollableRef.current;
        const header = headerRef.current;
        const inner = innerRef.current;
        invariant(outer);
        invariant(scrollable);
        invariant(header);
        invariant(inner);

        return columnRowDndHelpers.column({
            canDrag: canDragAndDrop,
            canDropRows: (row) =>
                "project_uid" in row &&
                row.project_uid === column.project_uid &&
                (mode === "manual" || ("project_column_uid" in row && row.project_column_uid !== column.uid)),
            column,
            symbolSet: BOARD_DND_SYMBOL_SET,
            draggable: header,
            dropTarget: outer,
            scrollable,
            settings: BOARD_DND_SETTINGS,
            setState,
            renderPreview({ container }) {
                // Simple drag preview generation: just cloning the current element.
                // Not using react for this.
                const rect = outer.getBoundingClientRect();
                const preview = outer.cloneNode(true);
                invariant(Utils.Type.isElement(preview, "div"));
                preview.classList.add("ring-2", "ring-primary");
                preview.style.width = `${rect.width}px`;
                preview.style.height = `${rect.height}px`;

                container.appendChild(preview);
            },
        });
    }, [canDragAndDrop, mode, column, order]);

    return (
        <BoardAddCardProvider column={column} viewportRef={scrollableRef} toLastPage={() => {}} isDefaultCardColumn={isDefaultCardColumn}>
            <WikiCardDrop column={column}>
                <CrossBoardDrop column={column}>
                    <Card.Root
                        ref={outerFullHeightRef}
                        data-board-column-sort={mode}
                        {...{ [BOARD_COLUMN_TOUCH_DND_ATTR]: column.uid }}
                        className={cn(
                            BOARD_COLUMN_MAX_HEIGHT_CLASS_NAMES,
                            "relative my-1 grid w-72 flex-shrink-0 snap-center grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-2xl",
                            "border-border/60 bg-muted/40 shadow-sm ring-primary dark:bg-background/60 sm:w-80",
                            stateStyles[state.type]
                        )}
                    >
                        {hasRunningBot && <ShineBorder />}
                        <BoardColumnHeader isDragging={state.type !== "idle"} column={column} headerProps={{ ref: headerRef }} />
                        <ScrollArea.Root
                            className="min-h-0 flex-1"
                            viewportRef={scrollableRef}
                            viewportClassName="!overflow-y-auto overscroll-contain touch-pan-y"
                            mutable={`${state.type}:${cardCount}`}
                        >
                            <Card.Content
                                className="flex min-h-0 touch-pan-y flex-col gap-2 p-3"
                                {...{ [BLOCK_BOARD_PANNING_ATTR]: true }}
                                ref={innerRef}
                            >
                                <BoardColumnCardList
                                    column={column}
                                    updateBoard={updateBoard}
                                    scrollableRef={scrollableRef}
                                    onCardCountChange={setCardCount}
                                />
                                {state.type === "is-row-over" && !state.isOverChildRow && <BoardColumnCardShadow dragging={state.dragging} />}
                                <BoardColumnAddCard />
                            </Card.Content>
                        </ScrollArea.Root>
                        <Card.Footer className="shrink-0 px-3 py-2">
                            <BoardColumnAddCardButton />
                        </Card.Footer>
                    </Card.Root>
                </CrossBoardDrop>
            </WikiCardDrop>
        </BoardAddCardProvider>
    );
}

/**
 * A memoized component for rendering out the card.
 *
 * Created so that state changes to the column don't require all cards to be rendered
 */
interface IBoardColumnCardListProps extends IBoardColumnProps {
    scrollableRef: RefObject<HTMLDivElement | null>;
    onCardCountChange: (cardCount: number) => void;
}

export const BoardColumnCardList = memo(({ column, updateBoard, scrollableRef, onCardCountChange }: IBoardColumnCardListProps) => {
    const [t] = useTranslation();
    const {
        project,
        socket,
        filters,
        filterCard,
        shouldShowArchivedCard,
        filterCardMember,
        filterCardCreator,
        filterCardLabels,
        filterCardRelationships,
    } = useBoard();
    const { mode } = useColumnCardSort(project.uid, column.uid);
    const [sortRevision, refreshSort] = useReducer((x) => x + 1, 0);
    const updater = useReducer((x) => x + 1, 0);
    const [updated, forceUpdate] = updater;
    const sortCards = ProjectCard.Model.useModels((card) => card.project_column_uid === column.uid, [column, updated]);
    const cardCreatedHandlers = useMemo(
        () =>
            useBoardCardCreatedHandlers({
                projectUID: project.uid,
                columnUID: column.uid,
                callback: () => {
                    forceUpdate();
                },
            }),
        [project, column, forceUpdate]
    );
    const columnDeletedHandlers = useMemo(
        () =>
            useBoardUIColumnDeletedHandlers({
                project,
                callback: () => {
                    if (!column.is_archive) {
                        return;
                    }

                    forceUpdate();
                    updateBoard();
                },
            }),
        [project, column, updateBoard, forceUpdate]
    );
    const otherHandlers = useMemo(() => [cardCreatedHandlers, columnDeletedHandlers], [cardCreatedHandlers, columnDeletedHandlers]);
    const { rows: columnCards } = useRowReordered({
        type: "ProjectCard",
        eventNameParams: { uid: column.uid },
        topicId: project.uid,
        rowFilter: (model) => {
            return (
                model.project_column_uid === column.uid &&
                (!column.is_archive || shouldShowArchivedCard(model)) &&
                filterCard(model) &&
                filterCardMember(model) &&
                filterCardCreator(model) &&
                filterCardLabels(model) &&
                filterCardRelationships(model)
            );
        },
        rowDependencies: [filters, sortRevision, filterCard, filterCardMember, filterCardCreator, filterCardLabels, filterCardRelationships],
        columnUID: column.uid,
        socket,
        updater,
        otherHandlers,
    });

    useEffect(() => {
        onCardCountChange(columnCards.length);
    }, [columnCards.length, onCardCountChange]);

    const { currentUser } = useAuth();
    const collapsedChildren = useUserSettings("child_card_collapsed");
    const preferencePrefix = `${currentUser?.uid}:${project.uid}:`;
    const [temporaryReveal, setTemporaryReveal] = useState<{ prefix: string; uids: Set<string> }>();
    const hierarchyGroups = useMemo(() => buildBoardColumnCardHierarchy(sortColumnCards(columnCards, mode)), [columnCards, mode, sortRevision]);
    const indexedGroups = useMemo(() => hierarchyGroups.map(indexBoardColumnCardHierarchy), [hierarchyGroups]);
    const visibleGroups = useMemo(
        () =>
            indexedGroups.map((nodes) =>
                visibleBoardColumnCardHierarchy(
                    nodes,
                    collapsedChildren,
                    preferencePrefix,
                    temporaryReveal?.prefix === preferencePrefix ? temporaryReveal.uids : new Set<string>()
                )
            ),
        [indexedGroups, collapsedChildren, preferencePrefix, temporaryReveal]
    );
    const revealCardAncestors = useCallback(
        (cardUID: string) => {
            const uids = new Set<string>();
            for (const nodes of indexedGroups) {
                for (const uid of boardColumnCardAncestorUIDs(nodes, cardUID)) uids.add(uid);
            }
            setTemporaryReveal({ prefix: preferencePrefix, uids });
        },
        [indexedGroups, preferencePrefix]
    );
    const toggleChildren = (cardUID: string) => {
        if (!currentUser) return;
        const store = getUserSettingsStore();
        const key = preferencePrefix + cardUID;
        const effectivelyCollapsed =
            !!store.settings.child_card_collapsed?.[key] && !(temporaryReveal?.prefix === preferencePrefix && temporaryReveal.uids.has(cardUID));
        // An explicitly toggled temporary reveal becomes collapsed; other ancestor reveals remain usable.
        const next = effectivelyCollapsed
            ? toggleChildCardCollapsed(store.settings.child_card_collapsed, key)
            : { ...store.settings.child_card_collapsed, [key]: true as const };
        store.updateSettingsByKey("child_card_collapsed", next);
        setTemporaryReveal((current) => {
            if (!current) return current;
            const uids = new Set(current.uids);
            uids.delete(cardUID);
            return { ...current, uids };
        });
    };
    const childCollapseProps = (node: (typeof indexedGroups)[number][number]) => ({
        childCount: node.end - node.index - 1,
        childrenExpanded:
            !collapsedChildren?.[preferencePrefix + node.card.uid] ||
            (temporaryReveal?.prefix === preferencePrefix && temporaryReveal.uids.has(node.card.uid)),
        onToggleChildren: () => toggleChildren(node.card.uid),
    });
    const cardGroupIndices = useMemo(() => {
        const indices = new Map<string, number[]>();
        hierarchyGroups.forEach((group, index) => {
            [group.root, ...group.descendants.map(({ card }) => card)].forEach((card) => {
                const positions = indices.get(card.uid) ?? [];
                positions.push(index);
                indices.set(card.uid, positions);
            });
        });
        return indices;
    }, [hierarchyGroups]);

    const virtualizer = useVirtualizer({
        count: hierarchyGroups.length,
        getScrollElement: () => scrollableRef.current,
        estimateSize: (index) => estimateBoardColumnCardGroupHeight(visibleGroups[index] ?? []),
        overscan: 10,
        getItemKey: (index) => hierarchyGroups[index]?.root.uid ?? index,
    });
    useEffect(() => {
        // Reset cached estimates for offscreen groups; mounted groups retain DOM measurement.
        virtualizer.measure();
    }, [visibleGroups, virtualizer]);
    const virtualItems = virtualizer.getVirtualItems();
    const totalSize = virtualizer.getTotalSize();

    useEffect(() => {
        const locateCard = (event: Event) => {
            const { cardUID, columnUID, onLocated, focus } = (event as CustomEvent<IBoardCardLocationEventDetail>).detail;
            if (columnUID !== column.uid) return;
            // Passive overlay geometry queries must preserve collapsed visibility.
            if (focus) revealCardAncestors(cardUID);
            const positions = (cardGroupIndices.get(cardUID) ?? []).flatMap((index) => {
                const offset = virtualizer.getOffsetForIndex(index, "start");
                return offset ? [offset[0]] : [];
            });
            onLocated(positions, scrollableRef.current?.scrollTop ?? 0);
        };
        document.addEventListener(BOARD_CARD_LOCATION_EVENT, locateCard);
        return () => document.removeEventListener(BOARD_CARD_LOCATION_EVENT, locateCard);
    }, [cardGroupIndices, column, scrollableRef, virtualizer, revealCardAncestors]);

    const routeLocation = usePageLocation();
    const focusFrameRef = useRef(0);
    const highlightTimeoutRef = useRef(0);
    const highlightedCardRef = useRef<HTMLElement | null>(null);
    useEffect(
        () => () => {
            cancelAnimationFrame(focusFrameRef.current);
            window.clearTimeout(highlightTimeoutRef.current);
            highlightedCardRef.current?.removeAttribute("data-relationship-drop-target");
        },
        [routeLocation.pathname]
    );
    useEffect(() => {
        const focusCard = (event: Event) => {
            const { cardUID, columnUID, focus } = (event as CustomEvent<IBoardCardFocusEventDetail>).detail;
            if (columnUID !== column.uid) {
                return;
            }

            const indices = cardGroupIndices.get(cardUID);
            if (!indices?.length) {
                return;
            }
            cancelAnimationFrame(focusFrameRef.current);
            window.clearTimeout(highlightTimeoutRef.current);
            highlightedCardRef.current?.removeAttribute("data-relationship-drop-target");
            const groupIndex = indices.reduce((nearest, index) => {
                const offset = (candidate: number) =>
                    Math.abs((virtualizer.getOffsetForIndex(candidate, "start")?.[0] ?? 0) - (scrollableRef.current?.scrollTop ?? 0));
                return offset(index) < offset(nearest) ? index : nearest;
            });

            revealCardAncestors(cardUID);
            virtualizer.scrollToIndex(groupIndex, { align: "center" });
            let attempts = 0;
            const highlightWhenMounted = () => {
                const cardElement = scrollableRef.current?.querySelector<HTMLElement>(
                    `[data-index="${groupIndex}"] [${BOARD_CARD_TOUCH_DND_ATTR}="${CSS.escape(cardUID)}"]`
                );
                if (!cardElement && attempts++ < 30) {
                    focusFrameRef.current = requestAnimationFrame(highlightWhenMounted);
                    return;
                }
                if (!cardElement) {
                    return;
                }

                const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
                cardElement.scrollIntoView({ behavior, block: "center", inline: "center" });
                if (focus) cardElement.querySelector<HTMLButtonElement>("[data-board-card-open]")?.focus({ preventScroll: true });
                highlightedCardRef.current = cardElement;
                cardElement.setAttribute("data-relationship-drop-target", "true");
                highlightTimeoutRef.current = window.setTimeout(() => cardElement.removeAttribute("data-relationship-drop-target"), 1200);
            };
            focusFrameRef.current = requestAnimationFrame(highlightWhenMounted);
        };

        document.addEventListener(BOARD_CARD_FOCUS_EVENT, focusCard);
        return () => {
            document.removeEventListener(BOARD_CARD_FOCUS_EVENT, focusCard);
        };
    }, [column, cardGroupIndices, scrollableRef, virtualizer, revealCardAncestors]);

    const revealedRouteRef = useRef<string | undefined>(undefined);
    useEffect(() => {
        const [, routeKind, routeProjectUID, routeCardUID] = routeLocation.pathname.split("/");
        if (routeKind !== "board" || routeProjectUID !== project.uid || routeProjectUID !== column.project_uid || !routeCardUID) {
            revealedRouteRef.current = undefined;
            return;
        }
        if (revealedRouteRef.current === routeLocation.pathname) return;
        revealedRouteRef.current = undefined;
        if (!cardGroupIndices.has(routeCardUID)) return;
        // The hierarchy and native focus listener are ready. Locate the routed card
        // without taking keyboard focus from its viewer or persisting a collapse change.
        const frame = requestAnimationFrame(() => {
            revealedRouteRef.current = routeLocation.pathname;
            document.dispatchEvent(
                new CustomEvent<IBoardCardFocusEventDetail>(BOARD_CARD_FOCUS_EVENT, {
                    detail: { cardUID: routeCardUID, columnUID: column.uid, focus: false },
                })
            );
        });
        return () => cancelAnimationFrame(frame);
    }, [routeLocation.pathname, project.uid, column.uid, column.project_uid, cardGroupIndices]);

    return (
        <Box className="relative w-full flex-shrink-0" style={{ height: `${totalSize}px` }}>
            {sortCards.map((card) => (
                <CardSortSubscription key={card.uid} card={card} refresh={refreshSort} />
            ))}
            {virtualItems.map((virtualRow) => {
                const group = hierarchyGroups[virtualRow.index];
                if (!group) {
                    return null;
                }

                const visibleNodes = visibleGroups[virtualRow.index];
                return (
                    <Box
                        key={group.root.uid}
                        ref={virtualizer.measureElement}
                        data-index={virtualRow.index}
                        className="absolute left-0 top-0 w-full pb-2"
                        style={{ transform: `translateY(${virtualRow.start}px)` }}
                    >
                        {group.hasContainmentCycle && (
                            <p role="status" className="px-2 py-1 text-xs text-muted-foreground">
                                {t("board.Containment cycles display each card once. Execution blocking is separate.")}
                            </p>
                        )}
                        {group.descendants.length ? (
                            <Box className="rounded-2xl border border-border/70 bg-secondary/30 p-1.5 shadow-sm">
                                <BoardColumnCard card={group.root} {...childCollapseProps(visibleNodes[0])} />
                                {visibleNodes.length > 1 && (
                                    <Flex direction="col" gap="1" className="mt-1.5 border-t border-border/60 pt-1.5">
                                        {visibleNodes.slice(1).map((item) => (
                                            <BoardColumnCard
                                                key={item.card.uid}
                                                card={item.card}
                                                hierarchyDepth={item.depth}
                                                grouped
                                                {...childCollapseProps(item)}
                                            />
                                        ))}
                                    </Flex>
                                )}
                            </Box>
                        ) : (
                            <BoardColumnCard card={group.root} />
                        )}
                    </Box>
                );
            })}
        </Box>
    );
});

function CardSortSubscription({ card, refresh }: { card: ProjectCard.TModel; refresh: () => void }) {
    const relationships = card.useForeignFieldArray("relationships");
    useEffect(refresh, [relationships, refresh]);
    card.useField("updated_at", refresh);
    card.useField("created_at", refresh);
    card.useField("deadline_at", refresh);
    card.useField("priority", refresh);
    card.useField("member_uids", refresh);
    return null;
}

export default BoardColumn;
