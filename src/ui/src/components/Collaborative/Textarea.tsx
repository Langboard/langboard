import Textarea, { TextareaProps } from "@/components/base/Textarea";
import SyncBlocker from "@/components/Collaborative/SyncBlocker";
import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";
import { ICollaborativeTextCursor, useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import { cn, composeRefs } from "@/core/utils/ComponentUtils";
import { TEditorCollaborationType } from "@langboard/core/constants";
import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface ICollaborativeTextareaProps extends Omit<TextareaProps, "value" | "onChange"> {
    collaborationType?: TEditorCollaborationType;
    documentID?: string;
    field: string;
    section?: number | string;
    uid?: number | string;
    preserveSyncedValue?: bool;
    resetSyncedValueToDefault?: bool;
    onChange?: React.ChangeEventHandler<HTMLTextAreaElement>;
    onCollaborativeValueReady?: (updateValue: ((value: string) => void) | null) => void;
    onCollaborativeValueResetReady?: (resetValue: ((value: string) => void) | null) => void;
    onValueChange?: (value: string) => void;
}

interface IHighlightRect {
    height: number;
    left: number;
    top: number;
    width: number;
}

interface ICursorOverlayPosition {
    caretHeight: number;
    caretLeft: number;
    caretTop: number;
    highlightRects: IHighlightRect[];
}

const createMeasureMarker = () => {
    const marker = document.createElement("span");
    marker.style.display = "inline-block";
    marker.style.width = "0";
    marker.style.padding = "0";
    marker.style.margin = "0";
    marker.style.border = "0";
    marker.style.overflow = "hidden";
    marker.textContent = "\u200b";
    return marker;
};

const getPxValue = (value: string, fallback: number = 0) => {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const CollaborativeTextarea = React.forwardRef<HTMLTextAreaElement, ICollaborativeTextareaProps>(
    (
        {
            collaborationType,
            documentID,
            field,
            section,
            uid,
            defaultValue,
            disabled,
            preserveSyncedValue,
            resetSyncedValueToDefault,
            onChange,
            onSelect,
            onKeyUp,
            onClick,
            onFocus,
            onMouseUp,
            onPaste,
            onCollaborativeValueReady,
            onCollaborativeValueResetReady,
            onValueChange,
            className,
            children,
            readOnly,
            ...props
        },
        ref
    ) => {
        const [t] = useTranslation();
        const textareaRef = useRef<HTMLTextAreaElement>(null);
        const { isSynced, remoteCursors, resetValue, retrySync, updateSelection, value, updateValue } = useCollaborativeText({
            collaborationType,
            documentID,
            field,
            section,
            uid,
            defaultValue,
            disabled,
            preserveSyncedValue,
            resetSyncedValueToDefault,
            onValueChange,
        });
        const [cursorPositions, setCursorPositions] = useState<Record<number, ICursorOverlayPosition>>({});
        const [showSyncUnavailable, setShowSyncUnavailable] = useState(false);
        const isCollaborationEnabled = !!documentID || (!!collaborationType && uid !== undefined && uid !== null);
        const isWaitingForSync = isCollaborationEnabled && !disabled && !readOnly && !isSynced;
        const isTextareaDisabled = disabled || isWaitingForSync;

        useEffect(() => {
            if (!isWaitingForSync) {
                setShowSyncUnavailable(false);
                return;
            }

            const timeoutID = window.setTimeout(() => {
                setShowSyncUnavailable(true);
            }, 5_000);

            return () => {
                window.clearTimeout(timeoutID);
            };
        }, [isWaitingForSync]);

        useEffect(() => {
            onCollaborativeValueReady?.(updateValue);

            return () => {
                onCollaborativeValueReady?.(null);
            };
        }, [onCollaborativeValueReady, updateValue]);

        useEffect(() => {
            onCollaborativeValueResetReady?.(resetValue);

            return () => {
                onCollaborativeValueResetReady?.(null);
            };
        }, [onCollaborativeValueResetReady, resetValue]);

        const handleChange: React.ChangeEventHandler<HTMLTextAreaElement> = (event) => {
            updateValue(event.target.value);
            updateSelection(event.target.selectionStart, event.target.selectionEnd);
            onChange?.(event);
        };

        const handlePaste: React.ClipboardEventHandler<HTMLTextAreaElement> = (event) => {
            onPaste?.(event);
            if (event.defaultPrevented) {
                return;
            }

            const textarea = textareaRef.current;
            const pastedText = event.clipboardData.getData("text");
            if (!textarea || !pastedText) {
                return;
            }

            event.preventDefault();
            const selectionStart = textarea.selectionStart;
            const selectionEnd = textarea.selectionEnd;
            const nextValue = `${value.slice(0, selectionStart)}${pastedText}${value.slice(selectionEnd)}`;
            const nextSelection = selectionStart + pastedText.length;

            updateValue(nextValue);
            requestAnimationFrame(() => {
                textarea.setSelectionRange(nextSelection, nextSelection);
                updateSelection(nextSelection, nextSelection);
            });
        };

        const handleClick: React.MouseEventHandler<HTMLTextAreaElement> = (event) => {
            updateLocalSelection();
            onClick?.(event);
        };

        const handleFocus: React.FocusEventHandler<HTMLTextAreaElement> = (event) => {
            updateLocalSelection();
            onFocus?.(event);
        };

        const handleKeyUp: React.KeyboardEventHandler<HTMLTextAreaElement> = (event) => {
            updateLocalSelection();
            onKeyUp?.(event);
        };

        const handleSelect: React.ReactEventHandler<HTMLTextAreaElement> = (event) => {
            updateLocalSelection();
            onSelect?.(event);
        };

        const handleMouseUp: React.MouseEventHandler<HTMLTextAreaElement> = (event) => {
            updateLocalSelection();
            onMouseUp?.(event);
        };

        const updateLocalSelection = useCallback(() => {
            const textarea = textareaRef.current;
            if (!textarea) {
                return;
            }

            updateSelection(textarea.selectionStart, textarea.selectionEnd);
        }, [updateSelection]);

        useEffect(() => {
            updateLocalSelection();
        }, [updateLocalSelection]);

        useLayoutEffect(() => {
            const textarea = textareaRef.current;
            if (!textarea || !remoteCursors.length) {
                setCursorPositions({});
                return;
            }

            const styles = window.getComputedStyle(textarea);
            const mirror = document.createElement("div");
            const trackedStyles = [
                "boxSizing",
                "borderBottomWidth",
                "borderLeftWidth",
                "borderRightWidth",
                "borderTopWidth",
                "fontFamily",
                "fontSize",
                "fontWeight",
                "letterSpacing",
                "lineHeight",
                "paddingBottom",
                "paddingLeft",
                "paddingRight",
                "paddingTop",
                "textTransform",
                "whiteSpace",
                "wordBreak",
                "wordSpacing",
                "wordWrap",
            ] as const;

            mirror.style.position = "absolute";
            mirror.style.visibility = "hidden";
            mirror.style.overflow = "hidden";
            mirror.style.top = "0";
            mirror.style.left = "-9999px";
            mirror.style.width = `${textarea.offsetWidth}px`;
            mirror.style.whiteSpace = "pre-wrap";
            mirror.style.wordWrap = "break-word";
            trackedStyles.forEach((styleName) => {
                mirror.style[styleName] = styles[styleName];
            });

            const paddingLeft = getPxValue(styles.paddingLeft);
            const paddingRight = getPxValue(styles.paddingRight);
            const lineHeight = getPxValue(styles.lineHeight, getPxValue(styles.fontSize, 16) * 1.2);
            const innerRight = textarea.clientWidth - paddingRight;
            const nextPositions: Record<number, ICursorOverlayPosition> = {};
            document.body.appendChild(mirror);

            remoteCursors.forEach((cursor) => {
                const selectionStart = Math.min(cursor.selectionStart, cursor.selectionEnd);
                const selectionEnd = Math.max(cursor.selectionStart, cursor.selectionEnd);
                const startMarker = createMeasureMarker();
                const endMarker = createMeasureMarker();

                mirror.textContent = value.slice(0, selectionStart);
                mirror.appendChild(startMarker);
                mirror.appendChild(document.createTextNode(value.slice(selectionStart, selectionEnd)));
                mirror.appendChild(endMarker);

                const startLeft = startMarker.offsetLeft - textarea.scrollLeft;
                const startTop = startMarker.offsetTop - textarea.scrollTop;
                const endLeft = endMarker.offsetLeft - textarea.scrollLeft;
                const endTop = endMarker.offsetTop - textarea.scrollTop;
                const highlightRects: IHighlightRect[] = [];

                if (selectionStart !== selectionEnd) {
                    if (startTop === endTop) {
                        highlightRects.push({
                            height: lineHeight,
                            left: startLeft,
                            top: startTop,
                            width: Math.max(endLeft - startLeft, 0),
                        });
                    } else {
                        highlightRects.push({
                            height: lineHeight,
                            left: startLeft,
                            top: startTop,
                            width: Math.max(innerRight - startLeft, 0),
                        });

                        for (let top = startTop + lineHeight; top < endTop; top += lineHeight) {
                            highlightRects.push({
                                height: lineHeight,
                                left: paddingLeft,
                                top,
                                width: Math.max(innerRight - paddingLeft, 0),
                            });
                        }

                        highlightRects.push({
                            height: lineHeight,
                            left: paddingLeft,
                            top: endTop,
                            width: Math.max(endLeft - paddingLeft, 0),
                        });
                    }
                }

                nextPositions[cursor.clientID] = {
                    caretHeight: lineHeight,
                    caretLeft: endLeft,
                    caretTop: endTop,
                    highlightRects,
                };
            });

            document.body.removeChild(mirror);
            setCursorPositions(nextPositions);
        }, [remoteCursors, value]);

        return (
            <div className="relative w-full">
                <Textarea
                    {...props}
                    ref={composeRefs(ref, textareaRef)}
                    disabled={isTextareaDisabled}
                    readOnly={readOnly}
                    value={value}
                    className={className}
                    onChange={handleChange}
                    onClick={handleClick}
                    onFocus={handleFocus}
                    onKeyUp={handleKeyUp}
                    onMouseUp={handleMouseUp}
                    onPaste={handlePaste}
                    onSelect={handleSelect}
                />
                {children}
                {isWaitingForSync && (
                    <SyncBlocker
                        actionLabel={showSyncUnavailable ? t("common.Refresh") : undefined}
                        label={t(showSyncUnavailable ? "common.Realtime sync unavailable." : "common.Syncing draft...")}
                        onAction={showSyncUnavailable ? retrySync : undefined}
                    />
                )}
                <RemoteCursors cursors={remoteCursors} positions={cursorPositions} />
            </div>
        );
    }
);

CollaborativeTextarea.displayName = "Collaborative.Textarea";

export default CollaborativeTextarea;

function RemoteCursors({ cursors, positions }: { cursors: ICollaborativeTextCursor[]; positions: Record<number, ICursorOverlayPosition> }) {
    return (
        <>
            {cursors.map((cursor) => {
                const position = positions[cursor.clientID];
                if (!position) {
                    return null;
                }

                return (
                    <React.Fragment key={cursor.clientID}>
                        {position.highlightRects.map((rect, index) => (
                            <span
                                key={`${cursor.clientID}-${index}`}
                                className="pointer-events-none absolute z-[19] rounded-sm opacity-25"
                                style={{
                                    backgroundColor: cursor.color,
                                    height: rect.height,
                                    left: rect.left,
                                    top: rect.top,
                                    width: rect.width,
                                }}
                            />
                        ))}
                        <span
                            className={cn(
                                "pointer-events-none absolute z-[20] w-0.5",
                                "after:absolute after:left-0 after:top-0 after:whitespace-nowrap"
                            )}
                            style={{
                                backgroundColor: cursor.color,
                                height: position.caretHeight,
                                left: position.caretLeft,
                                top: position.caretTop,
                            }}
                        >
                            <CollaborativeUserLabel
                                className="absolute left-0 top-0 z-[21] -translate-y-full whitespace-nowrap"
                                color={cursor.color}
                                name={cursor.name}
                            />
                        </span>
                    </React.Fragment>
                );
            })}
        </>
    );
}
