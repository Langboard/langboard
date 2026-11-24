import { formatNumber } from "@/core/utils/LocaleFormat";
import Box from "@/components/base/Box";
import HoverCard from "@/components/base/HoverCard";
import { cn } from "@/core/utils/ComponentUtils";
import { useTranslation } from "react-i18next";
import { memo, useMemo, useLayoutEffect, useRef, useState, useId } from "react";

import type { IDescriptionChunk } from "./descriptionChunks";
import { buildRailMarkers, getMarkerOpacity, getMarkerWidth, getNearestMarkerIndex, getRailGeometry } from "./descriptionOverviewRailData";

interface IDescriptionOverviewRailProps {
    chunks: IDescriptionChunk[];
    activeIndex: number;
    onNavigate: (index: number) => void;
    viewportHeight: number;
    scrollParentRef?: React.RefObject<HTMLDivElement | null>;
    onScrollKey?: (key: string) => boolean;
}

export function getPreviewTitleKey(type: IDescriptionChunk["metadata"]["type"]): string {
    switch (type) {
        case "code":
            return "editor.Code";
        case "table":
            return "editor.Table";
        case "list":
            return "editor.Lists";
        case "quote":
            return "editor.Quote";
        case "media":
            return "editor.Image";
        case "mixed":
            return "editor.Text";
        default:
            return "editor.Paragraph";
    }
}

export const DescriptionOverviewRail = memo(
    ({ chunks, activeIndex, onNavigate, viewportHeight, onScrollKey, scrollParentRef }: IDescriptionOverviewRailProps): React.JSX.Element | null => {
        const [t, i18n] = useTranslation();
        const [metrics, setMetrics] = useState({ height: viewportHeight, total: 0, top: 0 });
        const viewportId = useId();
        const drag = useRef<{ pointer: number; y: number; top: number } | null>(null);
        useLayoutEffect(() => {
            const viewport = scrollParentRef?.current;
            if (!viewport) return;
            const assignedId = !viewport.id;
            if (assignedId) viewport.id = viewportId;
            let frame = 0;
            const measure = () => {
                frame = 0;
                const next = { height: viewport.clientHeight, total: viewport.scrollHeight, top: viewport.scrollTop };
                setMetrics((previous) =>
                    previous.height === next.height && previous.total === next.total && previous.top === next.top ? previous : next
                );
            };
            const schedule = () => {
                if (!frame) frame = requestAnimationFrame(measure);
            };
            const resize = new ResizeObserver(schedule);
            const observe = () => {
                resize.disconnect();
                resize.observe(viewport);
                Array.from(viewport.children).forEach((child) => resize.observe(child));
                schedule();
            };
            const mutation = new MutationObserver(observe);
            mutation.observe(viewport, { subtree: true, childList: true, characterData: true, attributes: true });
            observe();
            measure();
            viewport.addEventListener("scroll", schedule, { passive: true });
            viewport.addEventListener("load", schedule, true);
            document.fonts?.addEventListener("loadingdone", schedule);
            return () => {
                if (assignedId && viewport.id === viewportId) viewport.removeAttribute("id");
                cancelAnimationFrame(frame);
                resize.disconnect();
                mutation.disconnect();
                viewport.removeEventListener("scroll", schedule);
                viewport.removeEventListener("load", schedule, true);
                document.fonts?.removeEventListener("loadingdone", schedule);
            };
        }, [scrollParentRef, chunks, viewportId]);
        const geometry = getRailGeometry(metrics.height, metrics.total, metrics.top);
        const markers = useMemo(() => buildRailMarkers(chunks, Math.max(1, Math.floor(geometry.height / 14))), [chunks, geometry.height]);
        const nearestActiveMarkerIndex = useMemo(() => getNearestMarkerIndex(markers, activeIndex), [activeIndex, markers]);

        if (!geometry.visible) return null;
        return (
            <Box
                data-card-description-rail
                position="absolute"
                top="6"
                left="1"
                className="pointer-events-none z-10 w-7"
                style={{ height: geometry.height }}
                aria-hidden={false}
                onKeyDown={(event) => {
                    if (event.target !== event.currentTarget && (event.target as HTMLElement).getAttribute("role") !== "scrollbar") {
                        if (onScrollKey?.(event.key)) event.preventDefault();
                        return;
                    }
                    const viewport = scrollParentRef?.current;
                    if (!viewport) return;
                    const offsets: Record<string, number> = {
                        Home: 0,
                        End: geometry.scrollRange,
                        ArrowUp: viewport.scrollTop - 40,
                        ArrowDown: viewport.scrollTop + 40,
                        PageUp: viewport.scrollTop - viewport.clientHeight * 0.9,
                        PageDown: viewport.scrollTop + viewport.clientHeight * 0.9,
                    };
                    if (event.key in offsets) {
                        event.preventDefault();
                        viewport.scrollTop = offsets[event.key];
                    }
                }}
            >
                <div
                    role="scrollbar"
                    aria-label={t("editor.Navigate description", { position: activeIndex + 1, total: chunks.length })}
                    aria-orientation="vertical"
                    aria-controls={scrollParentRef?.current?.id}
                    aria-valuemin={0}
                    aria-valuemax={Math.round(geometry.scrollRange)}
                    aria-valuenow={Math.round(metrics.top)}
                    tabIndex={0}
                    className={cn(
                        "pointer-events-auto absolute inset-y-0 left-0 w-3 touch-none rounded-full",
                        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
                    )}
                    onPointerDown={(event) => {
                        if (event.button !== 0) return;
                        event.preventDefault();
                        event.stopPropagation();
                        const viewport = scrollParentRef?.current;
                        if (!viewport) return;
                        const y = event.clientY - event.currentTarget.getBoundingClientRect().top;
                        if (y < geometry.thumbTop || y > geometry.thumbTop + geometry.thumbHeight) {
                            viewport.scrollTop =
                                ((y - geometry.thumbHeight / 2) / Math.max(1, geometry.height - geometry.thumbHeight)) * geometry.scrollRange;
                        }
                        drag.current = { pointer: event.pointerId, y: event.clientY, top: viewport.scrollTop };
                        event.currentTarget.setPointerCapture(event.pointerId);
                        event.currentTarget.focus();
                    }}
                    onPointerMove={(event) => {
                        const viewport = scrollParentRef?.current;
                        if (!viewport || drag.current?.pointer !== event.pointerId) return;
                        viewport.scrollTop =
                            drag.current.top +
                            ((event.clientY - drag.current.y) / Math.max(1, geometry.height - geometry.thumbHeight)) * geometry.scrollRange;
                    }}
                    onPointerUp={() => {
                        drag.current = null;
                    }}
                    onPointerCancel={() => {
                        drag.current = null;
                    }}
                    onLostPointerCapture={() => {
                        drag.current = null;
                    }}
                >
                    <div className="absolute inset-y-0 left-1 w-px bg-muted-foreground/20" />
                    <div
                        data-card-description-thumb
                        className="absolute left-[3px] w-[3px] rounded-full bg-foreground/60 forced-colors:bg-[CanvasText]"
                        style={{ top: geometry.thumbTop, height: geometry.thumbHeight }}
                    />
                </div>
                <Box className="pointer-events-auto absolute inset-y-0 right-0 flex w-3 flex-col items-end justify-between">
                    {markers.map((marker, markerIndex) => {
                        const distance = Math.abs(markerIndex - nearestActiveMarkerIndex);
                        const active = distance === 0;
                        const rangeLabel = marker.range
                            ? `${formatNumber(marker.range.start, i18n.language)}–${formatNumber(marker.range.end, i18n.language)}`
                            : `${formatNumber(marker.index + 1, i18n.language)} / ${formatNumber(chunks.length, i18n.language)}`;

                        return (
                            <HoverCard.Root key={`${marker.chunk.id}-${marker.index}`} openDelay={180} closeDelay={80}>
                                <HoverCard.Trigger asChild>
                                    <button
                                        type="button"
                                        aria-label={t("editor.Navigate description", { position: marker.index + 1, total: chunks.length })}
                                        aria-current={active ? "location" : undefined}
                                        className={cn(
                                            "relative flex h-3 min-h-0 w-3 shrink-0 items-center justify-end rounded-sm",
                                            "transition-[width,opacity,transform] duration-150 ease-out",
                                            "hover:opacity-100",
                                            "focus-visible:outline-none",
                                            "focus-visible:ring-2 focus-visible:ring-ring",
                                            "motion-reduce:transition-none",
                                            getMarkerOpacity(distance),
                                            active && "text-foreground"
                                        )}
                                        onPointerDown={(event) => event.stopPropagation()}
                                        onClick={(event) => {
                                            event.stopPropagation();
                                            onNavigate(marker.index);
                                        }}
                                    >
                                        <span
                                            className={cn(
                                                "block h-[2px] max-w-3 rounded-full bg-muted-foreground forced-colors:bg-[CanvasText]",
                                                getMarkerWidth(distance),
                                                active && "bg-foreground"
                                            )}
                                        />
                                    </button>
                                </HoverCard.Trigger>

                                <HoverCard.Portal>
                                    <HoverCard.Content
                                        side="right"
                                        align="center"
                                        sideOffset={10}
                                        className="w-72 max-w-[calc(100vw-3rem)]"
                                        onPointerDown={(event) => event.stopPropagation()}
                                    >
                                        <div className="truncate text-sm font-semibold">
                                            {marker.chunk.metadata.heading || t(getPreviewTitleKey(marker.chunk.metadata.type))}
                                        </div>
                                        {!!marker.chunk.metadata.previewText && (
                                            <div className="mt-1 line-clamp-3 text-xs leading-5 text-muted-foreground">
                                                {marker.chunk.metadata.previewText}
                                            </div>
                                        )}
                                        <div className="mt-2 text-[11px] text-muted-foreground/80">{rangeLabel}</div>
                                    </HoverCard.Content>
                                </HoverCard.Portal>
                            </HoverCard.Root>
                        );
                    })}
                </Box>
            </Box>
        );
    }
);

export default DescriptionOverviewRail;
