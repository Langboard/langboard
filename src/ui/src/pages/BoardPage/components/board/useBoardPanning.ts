import { useEffect } from "react";
import { bindAll } from "bind-event-listener";
import { BLOCK_BOARD_PANNING_ATTR } from "./BoardConstants";

/** Background panning, with deliberate outward pulls at viewport boundaries. */
export default function useBoardPanning(scrollableRef: React.RefObject<HTMLDivElement | null>, onPullLeft?: () => void, onPullRight?: () => void) {
    useEffect(() => {
        const scrollable = scrollableRef.current;
        if (!scrollable) return;
        let cleanupActive: (() => void) | undefined;
        const cleanupStart = bindAll(scrollable, [
            {
                type: "pointerdown",
                listener(start) {
                    if (!(start.target instanceof HTMLElement) || start.target.closest(`[${BLOCK_BOARD_PANNING_ATTR}]`)) return;
                    cleanupActive?.();
                    let lastX = start.clientX;
                    const bounds = scrollable.getBoundingClientRect();
                    const canPull =
                        start.isPrimary &&
                        start.button === 0 &&
                        start.pointerType === "mouse" &&
                        window.matchMedia("(min-width: 768px) and (pointer: fine)").matches &&
                        !start.target.closest("button, a, input, textarea, select, [contenteditable], [role=button]");
                    let pullLeft = canPull && !!onPullLeft && scrollable.scrollLeft <= 1 && start.clientX - bounds.left <= 32;
                    let pullRight =
                        canPull &&
                        !!onPullRight &&
                        scrollable.scrollWidth - scrollable.clientWidth - scrollable.scrollLeft <= 1 &&
                        bounds.right - start.clientX <= 32;
                    // A compose gesture must not submit an existing draft as an outside click.
                    if (pullLeft || pullRight) start.stopPropagation();
                    const cleanupEvents = bindAll(
                        window,
                        [
                            {
                                type: "pointermove",
                                listener(event) {
                                    if (event.pointerId !== start.pointerId) return;
                                    if (Math.abs(event.clientY - start.clientY) > 32) pullLeft = pullRight = false;
                                    scrollable.scrollBy({ left: lastX - event.clientX });
                                    lastX = event.clientX;
                                },
                            },
                            {
                                type: "pointerup",
                                listener(event) {
                                    if (event.pointerId !== start.pointerId) return;
                                    cleanupEvents();
                                    if (Math.abs(event.clientY - start.clientY) > 32) return;
                                    if (pullLeft && event.clientX - start.clientX >= 96) onPullLeft?.();
                                    if (pullRight && start.clientX - event.clientX >= 96) onPullRight?.();
                                },
                            },
                            ...(["pointercancel", "pointerdown", "keydown", "resize", "click", "visibilitychange"] as const).map((type) => ({
                                type,
                                listener: () => cleanupEvents(),
                            })),
                        ],
                        { capture: true }
                    );
                    cleanupActive = cleanupEvents;
                },
            },
        ]);
        return () => {
            cleanupStart();
            cleanupActive?.();
        };
    }, [scrollableRef, onPullLeft, onPullRight]);
}
