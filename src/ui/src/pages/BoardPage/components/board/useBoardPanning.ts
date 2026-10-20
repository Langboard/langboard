import { useEffect } from "react";
import { bindAll } from "bind-event-listener";
import { BLOCK_BOARD_PANNING_ATTR } from "./BoardConstants";

/** Background panning, with a deliberate outward pull at the left boundary. */
export default function useBoardPanning(scrollableRef: React.RefObject<HTMLDivElement | null>, onPullLeft?: () => void) {
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
                    let pull =
                        !!onPullLeft &&
                        start.isPrimary &&
                        start.button === 0 &&
                        start.pointerType === "mouse" &&
                        window.matchMedia("(min-width: 768px) and (pointer: fine)").matches &&
                        scrollable.scrollLeft <= 1 &&
                        start.clientX - scrollable.getBoundingClientRect().left <= 32 &&
                        !start.target.closest("button, a, input, textarea, select, [contenteditable], [role=button]");
                    // A compose gesture must not submit an existing draft as an outside click.
                    if (pull) start.stopPropagation();
                    const cleanupEvents = bindAll(
                        window,
                        [
                            {
                                type: "pointermove",
                                listener(event) {
                                    if (event.pointerId !== start.pointerId) return;
                                    if (Math.abs(event.clientY - start.clientY) > 32) pull = false;
                                    scrollable.scrollBy({ left: lastX - event.clientX });
                                    lastX = event.clientX;
                                },
                            },
                            {
                                type: "pointerup",
                                listener(event) {
                                    if (event.pointerId !== start.pointerId) return;
                                    cleanupEvents();
                                    if (pull && event.clientX - start.clientX >= 96 && Math.abs(event.clientY - start.clientY) <= 32) onPullLeft?.();
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
    }, [scrollableRef, onPullLeft]);
}
