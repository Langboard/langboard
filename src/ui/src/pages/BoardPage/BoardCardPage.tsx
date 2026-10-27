import { useBoardViewportId } from "@/core/providers/BoardViewportProvider";
import { useIsMobile } from "@/core/hooks/useIsMobile";
import Dialog from "@/components/base/Dialog";
import { isNotificationInteraction } from "@/components/Header/useNotificationNavigation";
import { CARD_WINDOW_EMBEDDED_OVERLAY_CLASS, CARD_WINDOW_HEIGHT_CLASS, CARD_WINDOW_OVERLAY_CLASS } from "./cardWindowLayout";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { useAuth } from "@/core/providers/AuthProvider";
import { ROUTES } from "@/core/routing/constants";
import { getEditorStore } from "@/core/stores/EditorStore";
import { cn } from "@/core/utils/ComponentUtils";
import { useCardFlipStore } from "@/pages/BoardPage/components/card/CardFlipStore";
import BoardCard from "@/pages/BoardPage/components/card/BoardCard";
import { flipDraftKey, useCardFlipDraftStore } from "./components/card/CardFlipDraftStore";
import { BoardCardSectionSaveProvider, useBoardCardSectionSaveActions } from "@/pages/BoardPage/components/card/BoardCardSectionSaveProvider";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Navigate, useParams } from "react-router";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { animate } from "framer-motion";
import { useBoardController } from "@/core/providers/BoardController";
import {
    CARD_ANIMATION_DURATION_MS,
    clearCardOpenAnimationSession,
    closedTransform,
    isRectCenterInside,
    markCardOpenAnimationPlayed,
    prefersReducedMotion,
    shouldPlayCardOpenAnimation,
    takeCardOrigin,
    type CardRect,
} from "@/pages/BoardPage/components/board/CardAnimation";

interface IBoardCardPageProps {
    projectUID?: string;
    cardUID?: string;
    embedded?: bool;
    isExpanded?: bool;
    setIsExpanded?: React.Dispatch<React.SetStateAction<bool>>;
}

const isWorkbenchInteraction = (target: EventTarget | null) =>
    target instanceof Element &&
    !!target.closest(
        "[data-workbench-sidebar], [data-workbench-explorer], [data-workbench-context], " +
            "[data-command-palette], [data-command-palette-trigger], [data-compact-label-preview]"
    );

const BoardCardPageComponent = ({
    projectUID: projectUIDProp,
    cardUID: cardUIDProp,
    embedded = false,
    isExpanded: controlledIsExpanded,
    setIsExpanded: controlledSetIsExpanded,
}: IBoardCardPageProps) => {
    const scrollportId = useBoardViewportId();
    const navigate = usePageNavigateRef();
    const location = usePageLocation();
    const { currentUser } = useAuth();
    const { saveSections } = useBoardCardSectionSaveActions();
    const params = useParams();
    const projectUID = projectUIDProp ?? params.projectUID;
    const cardUID = cardUIDProp ?? params.cardUID;
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const contentRef = useRef<HTMLDivElement | null>(null);
    const originRef = useRef<CardRect | null | undefined>(undefined);
    const closeTimerRef = useRef<number | null>(null);
    const motionRef = useRef<{ stop: () => void } | null>(null);
    const closingRef = useRef(false);
    const clearBackgroundPressRef = useRef<(() => void) | null>(null);
    const finishedCloseRef = useRef(false);
    const isCardEditingRef = useRef(false);
    const [isComposing, setIsComposing] = useState(false);
    const [localIsExpanded, setLocalIsExpanded] = useState(false);
    const isMobile = useIsMobile();
    const isExpanded = isMobile || (controlledIsExpanded ?? localIsExpanded);
    const setIsExpanded = controlledSetIsExpanded ?? setLocalIsExpanded;
    const { selectCardViewType } = useBoardController();
    const shouldHideForCardSelection = !!selectCardViewType;

    useLayoutEffect(() => {
        const content = contentRef.current;
        if (!content || !projectUID || !cardUID) {
            return;
        }

        if (originRef.current === undefined) {
            originRef.current = takeCardOrigin(projectUID, cardUID);
        }
        const sourceRect = originRef.current;
        const targetRect = content.getBoundingClientRect();
        if (sourceRect && targetRect.width > 0 && targetRect.height > 0) {
            content.style.setProperty("--card-origin-transform", closedTransform(sourceRect, targetRect));
        }
        // Mount-once gate: deep-link entry animates only the first mount of a
        // viewer session, so remounts (Suspense fallback swaps, provider
        // re-keys) cannot replay the entry animation. Never downgrade an
        // element that already started its animation.
        if (content.dataset.cardViewerReady !== "true" && content.dataset.cardViewerOpened !== "true") {
            const animateOpen = shouldPlayCardOpenAnimation(projectUID, cardUID, !!sourceRect);
            if (animateOpen) {
                markCardOpenAnimationPlayed(projectUID, cardUID);
            }
            content.dataset.cardViewerReady = animateOpen ? "true" : "false";
            if (animateOpen && sourceRect && !prefersReducedMotion()) {
                // A captured origin includes the Flip tray. Keep CSS entry for
                // cold deep links, but use the shared Motion runtime for restores.
                content.dataset.cardViewerReady = "false";
                content.dataset.cardViewerOpened = "true";
                motionRef.current = animate(
                    content,
                    { transform: [closedTransform(sourceRect, targetRect), "translate(0px, 0px) scale(1, 1)"], opacity: [0, 1] },
                    { duration: CARD_ANIMATION_DURATION_MS / 1000, ease: [0.2, 0.8, 0.2, 1] }
                );
            }
        }
    }, [projectUID, cardUID, currentUser]);

    useEffect(
        () => () => {
            motionRef.current?.stop();
            if (closeTimerRef.current !== null) {
                window.clearTimeout(closeTimerRef.current);
            }
        },
        []
    );

    useEffect(() => () => clearBackgroundPressRef.current?.(), [projectUID, cardUID]);

    const finishClose = () => {
        if (!projectUID || !cardUID || finishedCloseRef.current) {
            return;
        }
        finishedCloseRef.current = true;
        // The next open of this card is a fresh viewer session and animates again.
        clearCardOpenAnimationSession(projectUID, cardUID);
        if (closeTimerRef.current !== null) {
            window.clearTimeout(closeTimerRef.current);
            closeTimerRef.current = null;
        }
        navigate({
            pathname: ROUTES.BOARD.MAIN(projectUID),
            search: location.search,
        });
    };

    const close = (toTray = false) => {
        if (closingRef.current) {
            return;
        }
        closingRef.current = true;

        if (prefersReducedMotion()) {
            finishClose();
            return;
        }

        const source = toTray
            ? (document.querySelector(`[data-card-flip-item="${cardUID}"]`) ?? document.querySelector("[data-card-flip-tray]"))
            : document.getElementById(`board-card-${cardUID}`);
        const content = contentRef.current;
        const dockRect = source?.getBoundingClientRect();
        const workspace = document.getElementById(scrollportId)?.getBoundingClientRect() ?? document.querySelector("main")?.getBoundingClientRect();
        const sourceRect =
            toTray && dockRect
                ? {
                      left: Math.max(workspace?.left ?? 0, dockRect.left),
                      top: window.innerHeight - 56,
                      width: Math.min(dockRect.width, 116),
                      height: 40,
                  }
                : dockRect;
        const targetRect = content?.getBoundingClientRect();
        const windowRect = { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
        const boardRect = document.getElementById(scrollportId)?.getBoundingClientRect();
        if (
            sourceRect &&
            targetRect &&
            isRectCenterInside(sourceRect, windowRect) &&
            (toTray || !boardRect || isRectCenterInside(sourceRect, boardRect)) &&
            targetRect.width > 0 &&
            targetRect.height > 0
        ) {
            content?.style.setProperty("--card-origin-transform", closedTransform(sourceRect, targetRect));
        } else {
            content?.style.removeProperty("--card-origin-transform");
        }
        if (content) {
            motionRef.current?.stop();
            motionRef.current = animate(
                content,
                {
                    transform: ["translate(0px, 0px) scale(1, 1)", content.style.getPropertyValue("--card-origin-transform") || "scale(0.9)"],
                    opacity: [1, 0],
                },
                { duration: CARD_ANIMATION_DURATION_MS / 1000, ease: [0.2, 0.8, 0.2, 1], onComplete: finishClose }
            );
        }
        closeTimerRef.current = window.setTimeout(finishClose, CARD_ANIMATION_DURATION_MS + 50);
    };

    const handleCloseRequest = () => {
        // Ignore close request during IME composition to prevent double ESC handling
        if (isComposing) {
            return;
        }

        // Closing navigates away; section unmount flushes pending local drafts.
        // Only the explicit Cancel action discards an edit.
        close();
    };

    const handleEditModeStateChange = useCallback((isEditing: bool) => {
        isCardEditingRef.current = isEditing;
    }, []);

    // Detect IME composition state to handle Korean input properly
    useEffect(() => {
        const handleCompositionStart = () => setIsComposing(true);
        const handleCompositionEnd = () => setIsComposing(false);

        document.addEventListener("compositionstart", handleCompositionStart);
        document.addEventListener("compositionend", handleCompositionEnd);

        return () => {
            document.removeEventListener("compositionstart", handleCompositionStart);
            document.removeEventListener("compositionend", handleCompositionEnd);
        };
    }, []);

    const handleBackgroundPress: NonNullable<React.ComponentProps<typeof Dialog.Content>["onOverlayInteract"]> = (event) => {
        event.preventDefault();
        event.stopPropagation();
        const pointer = "nativeEvent" in event ? event.nativeEvent : event.detail.originalEvent;
        clearBackgroundPressRef.current?.();
        // Radix also forwards outside control pointers here. Apply the same
        // exemption before arming a release listener, including SVG icon targets.
        if (pointer.button !== 0 || pointer.buttons !== 1 || pointer.isPrimary === false || isWorkbenchInteraction(pointer.target)) return;

        const dismiss = () => {
            if (isNotificationInteraction(event.target)) return;
            if (isCardEditingRef.current) {
                handleCloseRequest();
                return;
            }
            if (isExpanded) return;
            if (getEditorStore().isInCurrentEditor()) {
                getEditorStore().setCurrentEditor(null);
                return;
            }
            // Keep the viewer mounted until the shared return animation finishes.
            handleCloseRequest();
        };
        const cleanup = () => {
            window.removeEventListener("pointerdown", cleanup, true);
            window.removeEventListener("mousedown", cancelButtons, true);
            window.removeEventListener("pointermove", move, true);
            window.removeEventListener("pointerup", release, true);
            window.removeEventListener("pointercancel", cancel, true);
            window.removeEventListener("scroll", cleanup, true);
            window.removeEventListener("blur", cleanup);
            clearBackgroundPressRef.current = null;
        };
        const cancelButtons = (next: MouseEvent) => {
            if (next.buttons !== 1) cleanup();
        };
        const move = (next: PointerEvent) => {
            if (
                next.pointerId === pointer.pointerId &&
                (next.buttons !== 1 || Math.hypot(next.clientX - pointer.clientX, next.clientY - pointer.clientY) > 6)
            ) {
                cleanup();
            }
        };
        const cancel = (next: PointerEvent) => {
            if (next.pointerId === pointer.pointerId) cleanup();
        };
        const release = (next: PointerEvent) => {
            if (next.pointerId !== pointer.pointerId) return;
            cleanup();
            if (next.target === pointer.target && next.button === 0 && next.buttons === 0) dismiss();
        };
        clearBackgroundPressRef.current = cleanup;
        // Any additional pointer makes this a multi-pointer gesture, even when
        // its target is inside the card and bypasses the backdrop handler.
        window.addEventListener("pointerdown", cleanup, true);
        window.addEventListener("mousedown", cancelButtons, true);
        window.addEventListener("pointermove", move, true);
        window.addEventListener("pointerup", release, true);
        window.addEventListener("pointercancel", cancel, true);
        window.addEventListener("scroll", cleanup, true);
        window.addEventListener("blur", cleanup);
    };

    if (!projectUID || !cardUID) {
        return <Navigate to={ROUTES.ERROR(EHttpStatus.HTTP_404_NOT_FOUND)} replace />;
    }

    return (
        <>
            {currentUser && cardUID && (
                <>
                    <Dialog.Root
                        modal={false}
                        open={true}
                        onOpenChange={(isOpen) => {
                            if (!isOpen && !selectCardViewType) {
                                handleCloseRequest();
                            }
                        }}
                    >
                        <Dialog.Content
                            ref={contentRef}
                            data-card-viewer=""
                            onPointerDownCapture={(event) => {
                                const target = event.target as Element;
                                // Dialog includes the transparent gap around the floating actions.
                                // Portalled controls belong to their own dismissable layer.
                                if (
                                    isExpanded ||
                                    shouldHideForCardSelection ||
                                    event.button !== 0 ||
                                    !event.currentTarget.contains(target) ||
                                    target.closest(
                                        "[data-card-surface], [data-floating-nav-content], [data-scroll-area-scrollbar], " +
                                            "button, a, input, textarea, [role=button]"
                                    ) ||
                                    isNotificationInteraction(target)
                                )
                                    return;
                                handleBackgroundPress(event);
                            }}
                            onCloseAutoFocus={(event) => {
                                // Route/card switches keep destination focus. Explicit dismissal
                                // returns to a visible card opener, or the workspace navigation.
                                event.preventDefault();
                                if (!finishedCloseRef.current || window.location.pathname !== ROUTES.BOARD.MAIN(projectUID!)) return;
                                if (document.querySelector("[data-card-viewer]")) return;
                                const source = document.getElementById(`board-card-${cardUID}`);
                                const sourceRect = source?.getBoundingClientRect();
                                const boardRect = document.getElementById(scrollportId)?.getBoundingClientRect();
                                const visible = sourceRect && boardRect && isRectCenterInside(sourceRect, boardRect);
                                const mobileTrigger = document.querySelector<HTMLButtonElement>("[data-mobile-navigation-trigger]");
                                const fallback = mobileTrigger?.getBoundingClientRect().width
                                    ? mobileTrigger
                                    : document.querySelector<HTMLButtonElement>("[data-command-palette-trigger]");
                                const opener = visible ? source?.querySelector<HTMLButtonElement>("[data-board-card-open]") : undefined;
                                (opener ?? fallback)?.focus({ preventScroll: true });
                            }}
                            disableMotionAnimation
                            onAnimationStart={(event) => {
                                if (event.target === event.currentTarget && event.animationName === "card-viewer-open") {
                                    // Freeze the entry animation to this element: an app-root
                                    // Suspense fallback swap hides the subtree and CSS restarts
                                    // animations when visibility is restored.
                                    event.currentTarget.dataset.cardViewerOpened = "true";
                                }
                            }}
                            className={cn(
                                "border-0 p-0 shadow-none",
                                isMobile
                                    ? cn(
                                          "pointer-events-auto inset-0 z-[110]",
                                          embedded
                                              ? "absolute bottom-[var(--workbench-bottom-inset,0px)] h-auto max-h-full"
                                              : "fixed h-dvh max-h-dvh",
                                          "w-screen max-w-none overflow-hidden rounded-none bg-background"
                                      )
                                    : isExpanded &&
                                          (embedded
                                              ? cn(
                                                    "pointer-events-auto absolute inset-0 z-[1]",
                                                    "h-full w-full max-w-none overflow-hidden",
                                                    "rounded-none bg-background"
                                                )
                                              : cn(
                                                    "pointer-events-auto fixed bottom-0 left-0 right-0 top-16",
                                                    "w-auto max-w-none overflow-hidden rounded-none bg-background",
                                                    "md:left-[var(--board-chat-sidebar-width,0px)]"
                                                )),
                                !isExpanded &&
                                    cn(
                                        "h-[calc(100dvh-theme(spacing.6))] max-h-[calc(100dvh-theme(spacing.6))]",
                                        "overflow-visible bg-transparent",
                                        "sm:h-[calc(100dvh-theme(spacing.8))] sm:max-h-[calc(100dvh-theme(spacing.8))]",
                                        embedded
                                            ? cn(
                                                  "w-[calc(100%-theme(spacing.4))] max-w-[calc(100%-theme(spacing.4))]",
                                                  "sm:w-[calc(100%-theme(spacing.12))] sm:max-w-[calc(100%-theme(spacing.12))]",
                                                  "2xl:max-w-screen-2xl lg:max-w-screen-xl"
                                              )
                                            : cn(
                                                  "w-[calc(100vw-theme(spacing.4))] max-w-[calc(100vw-theme(spacing.4))]",
                                                  "sm:w-[calc(100vw-theme(spacing.12))] sm:max-w-[calc(100vw-theme(spacing.12))]",
                                                  "lg:w-[min(calc(100vw-theme(spacing.12)),theme(screens.xl))]",
                                                  "lg:max-w-[min(calc(100vw-theme(spacing.12)),theme(screens.xl))]",
                                                  "2xl:w-[min(calc(100vw-theme(spacing.16)),theme(screens.2xl))]",
                                                  "2xl:max-w-[min(calc(100vw-theme(spacing.16)),theme(screens.2xl))]"
                                              )
                                    ),
                                !isExpanded && CARD_WINDOW_HEIGHT_CLASS,
                                shouldHideForCardSelection && "pointer-events-none -z-[9998] opacity-0"
                            )}
                            overlayClassName={
                                shouldHideForCardSelection
                                    ? "!pointer-events-none bg-transparent opacity-0 backdrop-blur-none"
                                    : isExpanded
                                      ? "!pointer-events-none !absolute !inset-0 !z-[1] bg-transparent backdrop-blur-none"
                                      : embedded
                                        ? CARD_WINDOW_EMBEDDED_OVERLAY_CLASS
                                        : CARD_WINDOW_OVERLAY_CLASS
                            }
                            overlayContentClassName={shouldHideForCardSelection || isExpanded ? "pointer-events-none" : undefined}
                            contentWrapperClassName={
                                shouldHideForCardSelection
                                    ? "pointer-events-none"
                                    : cn(
                                          "pointer-events-none [&_[data-dialog-content=true]]:pointer-events-auto",
                                          !isExpanded && "!items-start pb-2 pt-4 sm:pt-6",
                                          !isExpanded && "[&_[data-radix-scroll-area-viewport]>div]:!overflow-visible"
                                      )
                            }
                            viewportClassName={!isExpanded ? "!py-0" : undefined}
                            viewportAsTable={!embedded}
                            aria-describedby=""
                            withCloseButton={false}
                            nonModalOverlay
                            disablePortal={embedded}
                            viewportRef={viewportRef}
                            onInteractOutside={(event) => {
                                if (isMobile) {
                                    event.preventDefault();
                                    return;
                                }
                                if (isNotificationInteraction(event.detail.originalEvent.target)) {
                                    event.preventDefault();
                                    return;
                                }
                                if (isWorkbenchInteraction(event.detail.originalEvent.target)) {
                                    event.preventDefault();
                                    return;
                                }
                                if (isCardEditingRef.current) {
                                    event.preventDefault();
                                    return;
                                }

                                // Pointer dismissal waits for a completed background click.
                                if (isExpanded || event.detail.originalEvent.type === "pointerdown") event.preventDefault();
                            }}
                            onOverlayInteract={handleBackgroundPress}
                        >
                            <BoardCard
                                projectUID={projectUID}
                                cardUID={cardUID}
                                currentUser={currentUser}
                                viewportRef={viewportRef}
                                isExpanded={isExpanded}
                                setIsExpanded={isMobile ? undefined : setIsExpanded}
                                onClose={handleCloseRequest}
                                onFlip={async (card) => {
                                    if (isComposing || !currentUser || closingRef.current) return;
                                    if (isCardEditingRef.current) {
                                        const patch = await saveSections();
                                        if (patch === false) return;
                                        useCardFlipDraftStore.getState().save(flipDraftKey(currentUser.uid, projectUID, cardUID), {
                                            ...patch,
                                            deadline_at: patch.deadline_at instanceof Date ? patch.deadline_at.toISOString() : patch.deadline_at,
                                        });
                                    }
                                    useCardFlipStore
                                        .getState()
                                        .flip(currentUser.uid, projectUID, { uid: cardUID, title: card.linked_resource?.title ?? card.title });
                                    requestAnimationFrame(() => close(true));
                                }}
                                onEditModeStateChange={handleEditModeStateChange}
                            />
                        </Dialog.Content>
                    </Dialog.Root>
                </>
            )}
        </>
    );
};

const BoardCardPage = memo((props: IBoardCardPageProps) => {
    return (
        <BoardCardSectionSaveProvider>
            <BoardCardPageComponent {...props} />
        </BoardCardSectionSaveProvider>
    );
});

export default BoardCardPage;
