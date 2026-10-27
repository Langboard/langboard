import ProtectedPdfPreview from "./ProtectedPdfPreview";
import ImageAnnotations, { type ImageAnnotationContext } from "./ImageAnnotations";
import { useNativeImageComments } from "./NativeImageCommentContext";
import { formatNumber } from "@/core/utils/LocaleFormat";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import Label from "@/components/base/Label";
import CachedImage from "@/components/CachedImage";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import mimeTypes from "react-native-mime-types";
import { DismissableLayer } from "@radix-ui/react-dismissable-layer";
import { useTranslation } from "react-i18next";

export type FileItem = {
    url: string;
    type: string;
};

interface ImagePreviewDialogProps {
    files: { name: string; url: string; type?: string }[];
    initialIndex: number;
    initialPage?: number;
    sourcePages?: number[];
    onClose: () => void;
    annotationContext?: ImageAnnotationContext;
}

const ImagePreviewDialog = ({ files, initialIndex, initialPage, sourcePages, onClose, annotationContext }: ImagePreviewDialogProps) => {
    const [t, i18n] = useTranslation();
    const [currentIndex, setCurrentIndex] = useState(initialIndex);
    const [zoom, setZoom] = useState(1);
    const [pan, setPan] = useState({ x: 0, y: 0 });
    const annotationGestureActive = useRef(false);
    const onAnnotationGestureChange = useCallback((active: boolean) => {
        annotationGestureActive.current = active;
    }, []);
    const panStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
    const [image, setImage] = useState<{ element: HTMLImageElement; url: string | undefined } | null>(null);
    const native = useNativeImageComments();
    const currentFile = useMemo(() => files[currentIndex], [files, currentIndex]);
    const currentFileUrl = useMemo(() => currentFile?.url, [currentFile]);
    const currentFileName = useMemo(() => currentFile?.name, [currentFile]);
    const annotations = annotationContext ?? native?.contextForURL(currentFileUrl, onClose);
    const mimeType = useMemo(() => currentFile?.type || (!!currentFile && mimeTypes.lookup(currentFile.url)), [currentFile]);
    useEffect(() => {
        setPan({ x: 0, y: 0 });
    }, [currentFileUrl]);
    const updateZoom = (type: "in" | "out") => {
        if (annotationGestureActive.current) return;
        setZoom((prev) => {
            if (type === "in") {
                return Math.min(prev + 0.1, 1.5);
            } else {
                return Math.max(prev - 0.1, 0.5);
            }
        });
    };

    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Escape"].includes(e.key)) return;
            if (
                e.defaultPrevented ||
                (e.target instanceof Element && e.key !== "Escape" && e.target.closest("input, textarea, [contenteditable=true]"))
            )
                return;
            e.preventDefault();
            e.stopPropagation();

            if (e.key === "ArrowLeft") {
                goPrev();
            } else if (e.key === "ArrowRight") {
                goNext();
            } else if (e.key === "ArrowUp") {
                updateZoom("in");
            } else if (e.key === "ArrowDown") {
                updateZoom("out");
            } else if (e.key === "Escape") {
                onClose();
            }
        };

        window.addEventListener("keydown", handleKeyDown);

        return () => {
            window.removeEventListener("keydown", handleKeyDown);
        };
    }, [currentIndex, files]);

    const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
        if (mimeType === "application/pdf") return;
        if (e.deltaY < 0) {
            updateZoom("in");
        } else {
            updateZoom("out");
        }
    };

    const handleDrag = (e: React.DragEvent) => {
        e.preventDefault();
        e.stopPropagation();
    };

    const selectImage = (index: number) => {
        if (annotationGestureActive.current) return;
        setCurrentIndex(index);
        setZoom(1);
        setPan({ x: 0, y: 0 });
    };

    const goPrev = () => {
        selectImage(currentIndex > 0 ? currentIndex - 1 : files.length - 1);
    };

    const goNext = () => {
        selectImage(currentIndex + 1 < files.length ? currentIndex + 1 : 0);
    };

    return (
        <DismissableLayer disableOutsidePointerEvents>
            <Flex
                role="dialog"
                aria-label={t("editor.Image")}
                items="center"
                justify="center"
                position="fixed"
                z="50"
                className="inset-0 overflow-hidden bg-opacity-50 backdrop-blur-sm"
                onWheel={handleWheel}
                onClick={onClose}
            >
                <Box
                    onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                    }}
                >
                    <Flex
                        items="center"
                        justify="center"
                        p="4"
                        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, touchAction: zoom > 1 ? "none" : "auto" }}
                        onPointerDown={(event) => {
                            if (zoom <= 1 || !(event.target instanceof HTMLImageElement)) return;
                            event.preventDefault();
                            panStart.current = { x: event.clientX, y: event.clientY, left: pan.x, top: pan.y };
                            event.currentTarget.setPointerCapture(event.pointerId);
                        }}
                        onPointerMove={(event) => {
                            const start = panStart.current;
                            if (start) setPan({ x: start.left + event.clientX - start.x, y: start.top + event.clientY - start.y });
                        }}
                        onPointerUp={() => {
                            panStart.current = null;
                        }}
                        onPointerCancel={() => {
                            panStart.current = null;
                        }}
                    >
                        {mimeType === "application/pdf" ? (
                            <ProtectedPdfPreview url={currentFileUrl} name={currentFileName} initialPage={initialPage} pages={sourcePages} />
                        ) : !!mimeType && mimeType.startsWith("image/") ? (
                            <div className="relative inline-flex" data-image-annotation-layer>
                                <CachedImage
                                    key={currentFileUrl}
                                    onLoad={(e) => setImage({ element: e.currentTarget, url: currentFileUrl })}
                                    src={currentFileUrl}
                                    alt={currentFileName ?? t("editor.Image")}
                                    className={
                                        annotations
                                            ? "h-auto max-h-[calc(100dvh-10rem)] w-auto max-w-[calc(100vw-4rem)] object-contain"
                                            : "h-auto max-h-[calc(100dvh-4rem)] w-auto max-w-[calc(100vw-2rem)] object-contain"
                                    }
                                    onDragStart={handleDrag}
                                    onDragOver={handleDrag}
                                    onDragEnd={handleDrag}
                                    onDrag={handleDrag}
                                />
                                {annotations &&
                                    image?.element.isConnected &&
                                    image.url === currentFileUrl &&
                                    image.element.naturalWidth > 0 &&
                                    image.element.naturalWidth <= 100000 &&
                                    image.element.naturalHeight > 0 &&
                                    image.element.naturalHeight <= 100000 && (
                                        <ImageAnnotations
                                            key={`${currentFileUrl}-${annotations.source.attachment_uid}-${annotations.source.revision}`}
                                            context={annotations}
                                            image={image.element}
                                            onGestureChange={onAnnotationGestureChange}
                                        />
                                    )}
                            </div>
                        ) : (
                            <Flex items="center" direction="col">
                                <Box textSize="2xl" weight="semibold" mb="2" className="text-gray-500">
                                    {t("common.No preview")}
                                </Box>
                                <Box>{currentFileName}</Box>
                            </Flex>
                        )}
                    </Flex>
                    <Flex
                        items="center"
                        gap="2"
                        rounded="full"
                        border
                        position="fixed"
                        bottom="1"
                        z="50"
                        className="left-1/2 -translate-x-1/2 bg-secondary/70 [&>:first-child]:rounded-l-full [&>:last-child]:rounded-r-full"
                    >
                        <Label display="flex" items="center" ml="2" gap="0.5">
                            <Input
                                type="number"
                                value={currentIndex + 1}
                                min={1}
                                max={files.length}
                                onChange={(e) => selectImage(Math.max(0, Math.min(files.length - 1, Number(e.target.value) - 1)))}
                                variant="ghost"
                                className="h-auto w-10 p-0 text-center"
                            />
                            <span className="text-nowrap text-xs text-gray-500">/ {formatNumber(files.length, i18n.language)}</span>
                        </Label>
                        <Flex items="center">
                            <Button variant="ghost" size="icon-sm" onClick={goPrev}>
                                <IconComponent icon="chevron-left" size="4" />
                            </Button>
                            <Button variant="ghost" size="icon-sm" onClick={goNext}>
                                <IconComponent icon="chevron-right" size="4" />
                            </Button>
                        </Flex>
                        <Label display="flex" items="center" mr="2" gap="0.5">
                            <Input
                                type="number"
                                value={Math.round(zoom * 100)}
                                min={50}
                                max={150}
                                onChange={(e) => {
                                    if (annotationGestureActive.current) return;
                                    setZoom(Math.max(0.5, Math.min(1.5, Number(e.target.value) / 100)));
                                }}
                                variant="ghost"
                                className="h-auto w-10 p-0 text-center"
                            />
                            <span className="text-xs text-gray-500">%</span>
                        </Label>
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                                if (annotationGestureActive.current) return;
                                setZoom(1);
                                setPan({ x: 0, y: 0 });
                            }}
                        >
                            {t("card.Fit image")}
                        </Button>
                        <Button variant="ghost" size="icon-sm" onClick={onClose}>
                            <IconComponent icon="x" size="4" />
                        </Button>
                    </Flex>
                </Box>
            </Flex>
        </DismissableLayer>
    );
};

export default ImagePreviewDialog;
