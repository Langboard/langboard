"use client";

import { usePreviewImage, useImagePreview, useImagePreviewValue, useScaleInput } from "@platejs/media/react";
import useProtectedImage from "@/core/hooks/useProtectedImage";
import { cn } from "@/core/utils/ComponentUtils";
import { cva } from "class-variance-authority";
import { ArrowLeft, ArrowRight, Download, Minus, Plus, X } from "lucide-react";
import { useEditorRef } from "platejs/react";
import { DismissableLayer } from "@radix-ui/react-dismissable-layer";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

const buttonVariants = cva("rounded bg-[rgba(0,0,0,0.5)] px-1", {
    defaultVariants: {
        variant: "default",
    },
    variants: {
        variant: {
            default: "text-white",
            disabled: "cursor-not-allowed text-gray-400",
        },
    },
});

const SCROLL_SPEED = 4;

export function MediaPreviewDialog() {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const isOpen = useImagePreviewValue("isOpen", editor.id);
    const scale = useImagePreviewValue("scale");
    const isEditingScale = useImagePreviewValue("isEditingScale");
    const {
        closeProps,
        currentUrlIndex,
        maskLayerProps,
        nextDisabled,
        nextProps,
        prevDisabled,
        prevProps,
        scaleTextProps,
        zommOutProps,
        zoomInDisabled,
        zoomInProps,
        zoomOutDisabled,
    } = useImagePreview({ scrollSpeed: SCROLL_SPEED });

    if (!isOpen) return null;

    return createPortal(
        <DismissableLayer
            asChild
            onEscapeKeyDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
                closeProps.onClick();
            }}
        >
            <div
                role="dialog"
                aria-modal="true"
                aria-label={t("editor.Image")}
                className="fixed left-0 top-0 z-50 h-dvh w-screen select-none"
                onContextMenu={(e) => e.stopPropagation()}
                {...maskLayerProps}
            >
                <div className="absolute inset-0 size-full bg-black opacity-30"></div>
                <div className="absolute inset-0 size-full bg-black opacity-30"></div>
                <div className="absolute inset-0 flex items-center justify-center">
                    <div className="relative flex max-h-screen w-full items-center">
                        <ProtectedPreviewImage
                            className={cn(
                                "mx-auto block max-h-[calc(100dvh-4rem)] w-auto max-w-[calc(100vw-2rem)] object-contain transition-transform"
                            )}
                        />
                        <div
                            className="absolute bottom-0 left-1/2 z-40 flex w-fit -translate-x-1/2 justify-center gap-4 p-2 text-center text-white"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="flex gap-1">
                                <button
                                    {...prevProps}
                                    className={cn(
                                        buttonVariants({
                                            variant: prevDisabled ? "disabled" : "default",
                                        })
                                    )}
                                    type="button"
                                >
                                    <ArrowLeft />
                                </button>
                                {(currentUrlIndex ?? 0) + 1}
                                <button
                                    {...nextProps}
                                    className={cn(
                                        buttonVariants({
                                            variant: nextDisabled ? "disabled" : "default",
                                        })
                                    )}
                                    type="button"
                                >
                                    <ArrowRight />
                                </button>
                            </div>
                            <div className="flex">
                                <button
                                    className={cn(
                                        buttonVariants({
                                            variant: zoomOutDisabled ? "disabled" : "default",
                                        })
                                    )}
                                    {...zommOutProps}
                                    type="button"
                                >
                                    <Minus className="size-4" />
                                </button>
                                <div className="mx-px">
                                    {isEditingScale ? (
                                        <>
                                            <ScaleInput className="w-10 rounded px-1 text-slate-500 outline" /> <span>%</span>
                                        </>
                                    ) : (
                                        <span {...scaleTextProps}>{scale * 100 + "%"}</span>
                                    )}
                                </div>
                                <button
                                    className={cn(
                                        buttonVariants({
                                            variant: zoomInDisabled ? "disabled" : "default",
                                        })
                                    )}
                                    {...zoomInProps}
                                    type="button"
                                >
                                    <Plus className="size-4" />
                                </button>
                            </div>
                            {/* TODO: downLoad the image */}
                            <button className={cn(buttonVariants())} type="button">
                                <Download className="size-4" />
                            </button>
                            <button {...closeProps} aria-label={t("common.Close")} className={cn(buttonVariants())} type="button">
                                <X className="size-4" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </DismissableLayer>,
        document.body
    );
}

function ScaleInput(props: React.ComponentProps<"input">) {
    const { props: scaleInputProps, ref } = useScaleInput();

    return <input {...scaleInputProps} {...props} ref={ref} />;
}

function ProtectedPreviewImage({ className }: { className: string }) {
    const { props } = usePreviewImage();
    const image = useProtectedImage(props.src);
    // Plate types this image handler as a button event; its native PreviewImage forwards it to an img.
    const onClick = props.onClick as unknown as React.MouseEventHandler<HTMLImageElement>;
    return <img {...props} onClick={onClick} src={image.src} className={className} alt="" />;
}
