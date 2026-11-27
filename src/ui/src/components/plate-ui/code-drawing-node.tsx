"use client";

import * as React from "react";
import type { CodeDrawingType, TCodeDrawingElement, ViewMode } from "@platejs/code-drawing";
import { VIEW_MODE, DEFAULT_MIN_HEIGHT, CODE_DRAWING_TYPE_ARRAY, VIEW_MODE_ARRAY, downloadImage, DOWNLOAD_FILENAME } from "@platejs/code-drawing";
import type { PlateElementProps } from "platejs/react";
import { PlateElement, useEditorRef, useEditorSelector, useElement, useFocusedLast, useReadOnly, useSelected } from "platejs/react";
import { Trash2, DownloadIcon } from "lucide-react";
import { useIsMobile } from "@/core/hooks/useIsMobile";
import Button from "@/components/base/Button";
import Popover from "@/components/base/Popover";
import Select from "@/components/base/Select";
import { useTranslation } from "react-i18next";
import { canRenderDiagram, renderDiagram } from "@/core/helpers/CodeDrawingRenderer";

function useCodeDrawingElement({ element }: { element: TCodeDrawingElement }) {
    const editor = useEditorRef();
    const readOnly = useReadOnly();
    const [loading, setLoading] = React.useState(false);
    const [error, setError] = React.useState<string | null>(null);
    const [image, setImage] = React.useState<string>("");

    const lastRequestRef = React.useRef(0);

    React.useEffect(() => {
        const code = element.data?.code;
        const drawingType = element.data?.drawingType ?? "PlantUml";
        lastRequestRef.current += 1;
        const requestId = lastRequestRef.current;

        if (!code || !code.trim() || !drawingType) {
            setImage("");
            setLoading(false);
            setError(null);
            return;
        }

        const parsedDrawingType = drawingType as CodeDrawingType;
        if (!canRenderDiagram(parsedDrawingType, code)) {
            setImage("");
            setLoading(false);
            setError(null);
            return;
        }

        setLoading(true);
        setError(null);

        renderDiagram(parsedDrawingType, code)
            .then((imageData) => {
                if (lastRequestRef.current !== requestId) {
                    return;
                }

                setImage(imageData);
                setError(null);
            })
            .catch((err) => {
                if (lastRequestRef.current !== requestId) {
                    return;
                }

                setError(err instanceof Error ? err.message : "Rendering failed");
                setImage("");
            })
            .finally(() => {
                if (lastRequestRef.current === requestId) {
                    setLoading(false);
                }
            });
    }, [element.data?.code, element.data?.drawingType]);

    const removeNode = () => {
        if (readOnly) return;

        const path = editor.api.findPath(element);
        if (path) {
            editor.tf.removeNodes({ at: path });
        }
    };

    return {
        loading,
        error,
        image,
        removeNode,
    };
}

export function CodeDrawingElement(props: PlateElementProps<TCodeDrawingElement>) {
    const [t] = useTranslation();
    const isMobile = useIsMobile();
    const editor = useEditorRef();
    const readOnly = useReadOnly();
    const selected = useSelected();
    const isFocusedLast = useFocusedLast();
    const element = useElement<TCodeDrawingElement>();
    const { removeNode, image, loading } = useCodeDrawingElement({ element });

    const handleDownload = React.useCallback(() => {
        if (!image) return;
        downloadImage(image, DOWNLOAD_FILENAME);
    }, [image]);

    const handleCodeChange = React.useCallback(
        (code: string) => {
            const path = editor.api.findPath(element);
            if (path) {
                editor.tf.setNodes(
                    {
                        data: {
                            ...element.data,
                            code,
                        },
                    },
                    { at: path }
                );
            }
        },
        [editor, element]
    );

    const handleDrawingTypeChange = React.useCallback(
        (drawingType: CodeDrawingType) => {
            const path = editor.api.findPath(element);
            if (path) {
                editor.tf.setNodes(
                    {
                        data: {
                            ...element.data,
                            drawingType,
                        },
                    },
                    { at: path }
                );
            }
        },
        [editor, element]
    );

    const handleDrawingModeChange = React.useCallback(
        (drawingMode: ViewMode) => {
            const path = editor.api.findPath(element);
            if (path) {
                editor.tf.setNodes(
                    {
                        data: {
                            ...element.data,
                            drawingMode,
                        },
                    },
                    { at: path }
                );
            }
        },
        [editor, element]
    );

    const code = element.data?.code ?? "";
    const drawingType = element.data?.drawingType ?? "PlantUml";
    const drawingMode = element.data?.drawingMode ?? "Both";

    const selectionCollapsed = useEditorSelector((editor) => !editor.api.isExpanded(), []);

    const open = isFocusedLast && !readOnly && selected && selectionCollapsed;

    const content = (
        <PlateElement {...props}>
            <div contentEditable={false}>
                <CodeDrawingPreview
                    code={code}
                    drawingType={drawingType}
                    drawingMode={drawingMode}
                    image={image}
                    loading={loading}
                    onCodeChange={handleCodeChange}
                    onDrawingTypeChange={handleDrawingTypeChange}
                    onDrawingModeChange={handleDrawingModeChange}
                    readOnly={readOnly}
                    isMobile={isMobile}
                />
            </div>
        </PlateElement>
    );

    if (readOnly) {
        return content;
    }

    return (
        <Popover.Root open={open} modal={false}>
            <Popover.Anchor asChild>{content}</Popover.Anchor>
            <Popover.Content className="w-auto p-1" contentEditable={false} onOpenAutoFocus={(e) => e.preventDefault()}>
                <div className="flex items-center gap-1">
                    {image && (
                        <Button size="icon" variant="ghost" className="size-8" onClick={handleDownload} title={t("editor.Export")}>
                            <DownloadIcon className="size-4" />
                        </Button>
                    )}
                    <Button size="icon" variant="ghost" className="size-8" onClick={removeNode} title={t("editor.Delete")}>
                        <Trash2 className="size-4" />
                    </Button>
                </div>
            </Popover.Content>
        </Popover.Root>
    );
}

function CodeDrawingPreview({
    code,
    drawingType,
    drawingMode,
    image,
    loading,
    onCodeChange,
    onDrawingTypeChange,
    onDrawingModeChange,
    readOnly = false,
    isMobile = false,
}: {
    code: string;
    drawingType: CodeDrawingType;
    drawingMode: ViewMode;
    image: string;
    loading: bool;
    onCodeChange: (code: string) => void;
    onDrawingTypeChange: (type: CodeDrawingType) => void;
    onDrawingModeChange: (mode: ViewMode) => void;
    readOnly?: bool;
    isMobile?: bool;
}) {
    const viewMode = drawingMode;
    const showCode = viewMode === VIEW_MODE.Both || viewMode === VIEW_MODE.Code;
    const showBorder = viewMode === VIEW_MODE.Both;

    const handleCodeChange = React.useCallback(
        (e: React.ChangeEvent<HTMLTextAreaElement>) => {
            onCodeChange(e.target.value);
        },
        [onCodeChange]
    );

    const toolbar = readOnly ? null : (
        <CodeDrawingToolbar
            drawingType={drawingType}
            viewMode={viewMode}
            readOnly={readOnly}
            isMobile={isMobile}
            onDrawingTypeChange={onDrawingTypeChange}
            onDrawingModeChange={onDrawingModeChange}
        />
    );

    return (
        <div
            className={`flex ${isMobile ? "flex-col-reverse" : "flex-col"} group my-4 w-full items-stretch border bg-muted/50 md:flex-row`}
            style={{
                minHeight: `${DEFAULT_MIN_HEIGHT}px`,
            }}
        >
            {showCode && (
                <CodeDrawingTextarea
                    code={code}
                    viewMode={viewMode}
                    readOnly={readOnly}
                    isMobile={isMobile}
                    showBorder={showBorder}
                    onCodeChange={handleCodeChange}
                    toolbar={viewMode === VIEW_MODE.Code ? toolbar : null}
                />
            )}

            {viewMode !== VIEW_MODE.Code && (
                <CodeDrawingPreviewArea
                    image={image}
                    loading={loading}
                    code={code}
                    viewMode={viewMode}
                    readOnly={readOnly}
                    isMobile={isMobile}
                    showBorder={showBorder}
                    toolbar={toolbar}
                />
            )}
        </div>
    );
}

function CodeDrawingToolbar({
    drawingType,
    viewMode,
    readOnly = false,
    isMobile = false,
    onDrawingTypeChange,
    onDrawingModeChange,
}: {
    drawingType: CodeDrawingType;
    viewMode: ViewMode;
    readOnly?: bool;
    isMobile?: bool;
    onDrawingTypeChange: (type: CodeDrawingType) => void;
    onDrawingModeChange: (mode: ViewMode) => void;
}) {
    const [toolbarVisible, setToolbarVisible] = React.useState(false);
    const [languageSelectOpen, setLanguageSelectOpen] = React.useState(false);
    const [viewModeSelectOpen, setViewModeSelectOpen] = React.useState(false);

    const opacityClass = isMobile || toolbarVisible || languageSelectOpen || viewModeSelectOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100";

    const positionClass = isMobile ? "flex items-center gap-2" : "absolute right-2 z-10 flex items-center gap-2";

    return (
        <div
            role="toolbar"
            className={`${positionClass} transition-opacity ${opacityClass}`}
            onMouseEnter={() => setToolbarVisible(true)}
            onMouseLeave={() => {
                if (!languageSelectOpen && !viewModeSelectOpen) {
                    setToolbarVisible(false);
                }
            }}
        >
            {!readOnly && (
                <Select.Root value={drawingType} onValueChange={onDrawingTypeChange} open={languageSelectOpen} onOpenChange={setLanguageSelectOpen}>
                    <Select.Trigger
                        className={`h-8 w-[120px] border-0 bg-muted/50 text-xs shadow-none ${isMobile ? "" : "transition-colors hover:bg-zinc-200"}`}
                    >
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content
                        className="z-[100]"
                        onPointerDownOutside={(e) => {
                            (window as Window & { __lbIgnoreStopEditingUntil?: number }).__lbIgnoreStopEditingUntil = Date.now() + 150;
                            const originalEvent = (e as unknown as { detail?: { originalEvent?: Event } }).detail?.originalEvent;
                            originalEvent?.preventDefault();
                            originalEvent?.stopPropagation();
                            e.preventDefault();
                            setLanguageSelectOpen(false);
                        }}
                    >
                        {CODE_DRAWING_TYPE_ARRAY.map((item) => (
                            <Select.Item key={item.value} value={item.value}>
                                {item.label}
                            </Select.Item>
                        ))}
                    </Select.Content>
                </Select.Root>
            )}

            {!readOnly && (
                <Select.Root value={viewMode} onValueChange={onDrawingModeChange} open={viewModeSelectOpen} onOpenChange={setViewModeSelectOpen}>
                    <Select.Trigger
                        className={`h-8 w-[80px] border-0 bg-muted/50 text-xs shadow-none ${isMobile ? "" : "transition-colors hover:bg-zinc-200"}`}
                    >
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content
                        className="z-[100]"
                        onPointerDownOutside={(e) => {
                            (window as Window & { __lbIgnoreStopEditingUntil?: number }).__lbIgnoreStopEditingUntil = Date.now() + 150;
                            const originalEvent = (e as unknown as { detail?: { originalEvent?: Event } }).detail?.originalEvent;
                            originalEvent?.preventDefault();
                            originalEvent?.stopPropagation();
                            e.preventDefault();
                            setViewModeSelectOpen(false);
                        }}
                    >
                        {VIEW_MODE_ARRAY.map((item) => (
                            <Select.Item key={item.value} value={item.value}>
                                {item.label}
                            </Select.Item>
                        ))}
                    </Select.Content>
                </Select.Root>
            )}
        </div>
    );
}

function CodeDrawingTextarea({
    code,
    viewMode,
    readOnly = false,
    isMobile = false,
    showBorder = false,
    onCodeChange,
    toolbar,
}: {
    code: string;
    viewMode: ViewMode;
    readOnly?: bool;
    isMobile?: bool;
    showBorder?: bool;
    onCodeChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
    toolbar?: React.ReactNode;
}) {
    const [t] = useTranslation();
    const textareaRef = React.useRef<HTMLTextAreaElement>(null);
    const isCodeOnlyMode = viewMode === VIEW_MODE.Code;

    const [internalCode, setInternalCode] = React.useState(code);
    const lastExternalCodeRef = React.useRef(code);

    React.useEffect(() => {
        if (code !== lastExternalCodeRef.current) {
            lastExternalCodeRef.current = code;
            setInternalCode(code);
        }
    }, [code]);

    const handleChange = React.useCallback(
        (e: React.ChangeEvent<HTMLTextAreaElement>) => {
            const newValue = e.target.value;
            setInternalCode(newValue);
            onCodeChange(e);
        },
        [onCodeChange]
    );

    return (
        <div
            className={`${isCodeOnlyMode ? "w-full" : "min-w-0 flex-1"} flex flex-col ${isCodeOnlyMode && !isMobile ? "relative" : ""} ${
                showBorder && !isMobile ? "border-r" : ""
            }`}
        >
            {toolbar && isCodeOnlyMode && (
                <div className={isMobile ? "mb-2 mt-2 flex justify-end px-2" : "absolute right-2 z-10 mt-2"}>{toolbar}</div>
            )}

            <div className="relative flex-1 rounded-md">
                <pre
                    className={"m-0 overflow-x-auto p-8 pr-4 font-mono text-sm leading-[normal] [tab-size:2] print:break-inside-avoid"}
                    style={{ minHeight: `${DEFAULT_MIN_HEIGHT}px`, height: "100%" }}
                >
                    <code className="block h-full w-full">
                        <textarea
                            ref={textareaRef}
                            value={internalCode}
                            onChange={handleChange}
                            readOnly={readOnly}
                            className="m-0 h-full w-full resize-none overflow-auto border-0 bg-transparent p-0 font-mono text-sm outline-none"
                            style={{ minHeight: `${DEFAULT_MIN_HEIGHT}px` }}
                            placeholder={t("editor.Enter your code here...")}
                            spellCheck={false}
                        />
                    </code>
                </pre>
            </div>
        </div>
    );
}

function CodeDrawingPreviewArea({
    image,
    loading,
    code,
    viewMode,
    readOnly: _readOnly = false,
    isMobile = false,
    showBorder = false,
    toolbar,
}: {
    image: string;
    loading: bool;
    code: string;
    viewMode: ViewMode;
    readOnly?: bool;
    isMobile?: bool;
    showBorder?: bool;
    toolbar?: React.ReactNode;
}) {
    const [t] = useTranslation();
    const showImage = viewMode === VIEW_MODE.Both || viewMode === VIEW_MODE.Image;

    return (
        <div className={`flex min-w-0 flex-1 flex-col ${isMobile ? "" : "relative"} ${showBorder && isMobile ? "border-b" : ""}`}>
            {toolbar && <div className={isMobile ? "mb-2 mt-2 flex justify-end px-2" : "absolute right-2 z-10 mt-2"}>{toolbar}</div>}

            {showImage ? (
                <div className={"flex flex-1 items-center justify-center rounded-md bg-muted/30 p-4"}>
                    {loading && <div className="text-muted-foreground">{t("editor.Loading...")}</div>}
                    {!loading && image && <img src={image} alt={t("editor.Code drawing")} className="max-h-full max-w-full object-contain" />}
                    {!loading && !image && (
                        <div className="text-muted-foreground">{code.trim() ? t("editor.Rendering...") : t("editor.Preview will appear here")}</div>
                    )}
                </div>
            ) : (
                <div className="pointer-events-none flex flex-1 items-center justify-center rounded-md border bg-muted/30 p-4 opacity-0">
                    {/* Placeholder to maintain height */}
                </div>
            )}
        </div>
    );
}
