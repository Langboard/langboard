import { cn } from "@/core/utils/ComponentUtils";
import HoverCard from "@/components/base/HoverCard";
import Button from "@/components/base/Button";
import type { ICardCommentImageAnchor } from "@/core/models/types/card-comment-anchor.type";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface ImageAnnotationContext {
    source: ICardCommentImageAnchor["source"];
    comments: { uid: string; anchor: ICardCommentImageAnchor; preview: string; canEdit?: boolean }[];
    highlightedCommentUID?: string;
    onCreate: (anchor: ICardCommentImageAnchor) => void;
    onOpenComment: (uid: string) => void;
    onEditComment?: (uid: string) => void;
}

export default function ImageAnnotations({
    context,
    image,
    onGestureChange,
}: {
    context: ImageAnnotationContext;
    image: HTMLImageElement;
    onGestureChange: (active: boolean) => void;
}) {
    const [t] = useTranslation();
    const [mode, setMode] = useState<"point" | "rectangle" | null>(null);
    const [pending, setPending] = useState<ICardCommentImageAnchor | null>(null);
    const start = useRef<{ x: number; y: number; pointerId: number; target: HTMLDivElement } | null>(null);
    useEffect(() => () => onGestureChange(false), [onGestureChange]);
    const endGesture = () => {
        const gesture = start.current;
        start.current = null;
        onGestureChange(false);
        if (gesture?.target.hasPointerCapture(gesture.pointerId)) gesture.target.releasePointerCapture(gesture.pointerId);
    };
    const cancelGesture = () => {
        endGesture();
        setPending(null);
    };
    const coordinates = (x: number, y: number) => {
        const rect = image.getBoundingClientRect();
        return {
            x: Math.max(0, Math.min(image.naturalWidth, ((x - rect.left) / rect.width) * image.naturalWidth)),
            y: Math.max(0, Math.min(image.naturalHeight, ((y - rect.top) / rect.height) * image.naturalHeight)),
        };
    };
    const select = (from: { x: number; y: number }, to = from) => {
        setPending({
            type: "ImageRegionSelector",
            version: 1,
            source: context.source,
            shape: mode ?? "point",
            image_width: image.naturalWidth,
            image_height: image.naturalHeight,
            x: Math.min(from.x, to.x),
            y: Math.min(from.y, to.y),
            width: mode === "rectangle" ? Math.abs(from.x - to.x) : 0,
            height: mode === "rectangle" ? Math.abs(from.y - to.y) : 0,
        });
    };
    const style = (anchor: ICardCommentImageAnchor) => ({
        left: `${(anchor.x / anchor.image_width) * 100}%`,
        top: `${(anchor.y / anchor.image_height) * 100}%`,
        ...(anchor.shape === "rectangle"
            ? { width: `${(anchor.width / anchor.image_width) * 100}%`, height: `${(anchor.height / anchor.image_height) * 100}%` }
            : { width: 20, height: 20, transform: "translate(-50%, -50%)", borderRadius: "50%" }),
    });
    return (
        <>
            <div
                className="absolute inset-0"
                style={{ pointerEvents: mode ? "auto" : "none", touchAction: mode ? "none" : "auto" }}
                role={mode ? "button" : undefined}
                tabIndex={mode ? 0 : undefined}
                aria-label={mode ? t("card.Place image annotation") : undefined}
                onPointerDown={(e) => {
                    if (!mode || !e.isPrimary || e.button !== 0 || start.current) return;
                    e.preventDefault();
                    e.stopPropagation();
                    e.currentTarget.setPointerCapture(e.pointerId);
                    start.current = { ...coordinates(e.clientX, e.clientY), pointerId: e.pointerId, target: e.currentTarget };
                    onGestureChange(true);
                    select(start.current);
                }}
                onPointerMove={(e) => {
                    if (start.current?.pointerId === e.pointerId && mode === "rectangle") select(start.current, coordinates(e.clientX, e.clientY));
                }}
                onPointerUp={(e) => {
                    if (start.current?.pointerId !== e.pointerId) return;
                    select(start.current, mode === "rectangle" ? coordinates(e.clientX, e.clientY) : start.current);
                    endGesture();
                }}
                onPointerCancel={(e) => {
                    if (start.current?.pointerId !== e.pointerId) return;
                    cancelGesture();
                }}
                onLostPointerCapture={(e) => {
                    if (start.current?.pointerId === e.pointerId) cancelGesture();
                }}
                onKeyDown={(e) => {
                    if (!mode || (e.target !== e.currentTarget && e.key !== "Escape")) return;
                    if (e.key === "Escape") {
                        e.stopPropagation();
                        cancelGesture();
                        setMode(null);
                    }
                    if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        select(
                            { x: image.naturalWidth / 2, y: image.naturalHeight / 2 },
                            mode === "rectangle" ? { x: image.naturalWidth * 0.75, y: image.naturalHeight * 0.75 } : undefined
                        );
                    }
                    if (e.key.startsWith("Arrow")) {
                        e.preventDefault();
                        e.stopPropagation();
                        const x = Math.max(
                            0,
                            Math.min(
                                image.naturalWidth - (pending?.width ?? 0),
                                (pending?.x ?? image.naturalWidth / 2) + (e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0)
                            )
                        );
                        const y = Math.max(
                            0,
                            Math.min(
                                image.naturalHeight - (pending?.height ?? 0),
                                (pending?.y ?? image.naturalHeight / 2) + (e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0)
                            )
                        );
                        if (pending) setPending({ ...pending, x, y });
                        else select({ x, y });
                    }
                }}
            >
                {context.comments
                    .filter(
                        ({ anchor }) =>
                            anchor.source.revision === context.source.revision &&
                            anchor.image_width === image.naturalWidth &&
                            anchor.image_height === image.naturalHeight
                    )
                    .map(({ uid, anchor, preview, canEdit }, i) => (
                        <HoverCard.Root key={uid} openDelay={150}>
                            <HoverCard.Trigger asChild>
                                <Button
                                    size="icon-sm"
                                    variant="secondary"
                                    className={cn(
                                        "pointer-events-auto absolute min-h-0 min-w-0 border-2 border-brand p-0",
                                        context.highlightedCommentUID === uid && "ring-4 ring-brand"
                                    )}
                                    style={style(anchor)}
                                    aria-label={preview || t("card.Open anchored comment")}
                                    onPointerDown={(event) => event.stopPropagation()}
                                    onPointerUp={(event) => event.stopPropagation()}
                                    onClick={() => context.onOpenComment(uid)}
                                >
                                    {i + 1}
                                </Button>
                            </HoverCard.Trigger>
                            <HoverCard.Portal>
                                <HoverCard.Content
                                    className="pointer-events-auto z-[60] space-y-2"
                                    onPointerDown={(event) => event.stopPropagation()}
                                >
                                    <p className="whitespace-pre-wrap break-words text-sm">{preview || t("card.Open anchored comment")}</p>
                                    <Button size="sm" onClick={() => context.onOpenComment(uid)}>
                                        {t("card.Open anchored comment")}
                                    </Button>
                                    {canEdit && context.onEditComment && (
                                        <Button size="sm" onClick={() => context.onEditComment?.(uid)}>
                                            {t("common.Edit")}
                                        </Button>
                                    )}
                                </HoverCard.Content>
                            </HoverCard.Portal>
                        </HoverCard.Root>
                    ))}
                {pending && (
                    <div
                        data-pending-image-anchor
                        className="pointer-events-none absolute border-2 border-brand bg-brand/20"
                        style={style(pending)}
                    />
                )}
            </div>
            <div className="absolute left-0 top-0 flex -translate-y-full gap-2 bg-background p-2" onPointerDown={(e) => e.stopPropagation()}>
                <Button
                    size="sm"
                    variant="secondary"
                    aria-pressed={mode === "point"}
                    onClick={() => {
                        cancelGesture();
                        setMode("point");
                    }}
                >
                    {t("card.Image point")}
                </Button>
                <Button
                    size="sm"
                    variant="secondary"
                    aria-pressed={mode === "rectangle"}
                    onClick={() => {
                        cancelGesture();
                        setMode("rectangle");
                    }}
                >
                    {t("card.Image region")}
                </Button>
                {mode && (
                    <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => {
                            cancelGesture();
                            setMode(null);
                        }}
                    >
                        {t("common.Cancel")}
                    </Button>
                )}
                {pending && (
                    <Button
                        size="sm"
                        disabled={pending.shape === "rectangle" && (!pending.width || !pending.height)}
                        onClick={() => context.onCreate(pending)}
                    >
                        {t("card.Comment on selection")}
                    </Button>
                )}
            </div>
        </>
    );
}
