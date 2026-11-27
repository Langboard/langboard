import Input, { InputProps } from "@/components/base/Input";
import SyncBlocker from "@/components/Collaborative/SyncBlocker";
import CollaborativeUserLabel from "@/components/Collaborative/UserLabel";
import { ICollaborativeTextCursor, useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import { composeRefs } from "@/core/utils/ComponentUtils";
import { TEditorCollaborationType } from "@langboard/core/constants";
import React, { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export interface ICollaborativeInputProps extends Omit<InputProps, "value" | "onChange"> {
    collaborationType?: TEditorCollaborationType;
    documentID?: string;
    field: string;
    section?: number | string;
    uid?: number | string;
    preserveSyncedValue?: bool;
    resetSyncedValueToDefault?: bool;
    onChange?: React.ChangeEventHandler<HTMLInputElement>;
    onCollaborativeValueReady?: (updateValue: ((value: string) => void) | null) => void;
    onCollaborativeValueResetReady?: (resetValue: ((value: string) => void) | null) => void;
    onValueChange?: (value: string) => void;
}

interface ICursorOverlayPosition {
    caretHeight: number;
    caretLeft: number;
    highlightHeight: number;
    highlightLeft: number;
    highlightWidth: number;
    top: number;
}

const PASSWORD_MASK_CHARACTER = "\u2022";

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

const CollaborativeInput = React.forwardRef<HTMLInputElement, ICollaborativeInputProps>(
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
            readOnly,
            ...props
        },
        ref
    ) => {
        const [t] = useTranslation();
        const inputRef = useRef<HTMLInputElement>(null);
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
        const [inputType, setInputType] = useState("text");
        const [showSyncUnavailable, setShowSyncUnavailable] = useState(false);
        const isCollaborationEnabled = !!documentID || (!!collaborationType && uid !== undefined && uid !== null);
        const isWaitingForSync = isCollaborationEnabled && !disabled && !readOnly && !isSynced;
        const isInputDisabled = disabled || isWaitingForSync;

        React.useEffect(() => {
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

        React.useEffect(() => {
            onCollaborativeValueReady?.(updateValue);

            return () => {
                onCollaborativeValueReady?.(null);
            };
        }, [onCollaborativeValueReady, updateValue]);

        React.useEffect(() => {
            onCollaborativeValueResetReady?.(resetValue);

            return () => {
                onCollaborativeValueResetReady?.(null);
            };
        }, [onCollaborativeValueResetReady, resetValue]);

        const handleChange: React.ChangeEventHandler<HTMLInputElement> = (event) => {
            updateValue(event.target.value);
            updateSelection(event.target.selectionStart ?? 0, event.target.selectionEnd ?? event.target.selectionStart ?? 0);
            onChange?.(event);
        };

        const handlePaste: React.ClipboardEventHandler<HTMLInputElement> = (event) => {
            onPaste?.(event);
            if (event.defaultPrevented) {
                return;
            }

            const input = inputRef.current;
            const pastedText = event.clipboardData.getData("text");
            if (!input || !pastedText) {
                return;
            }

            event.preventDefault();
            const selectionStart = input.selectionStart ?? value.length;
            const selectionEnd = input.selectionEnd ?? selectionStart;
            const nextValue = `${value.slice(0, selectionStart)}${pastedText}${value.slice(selectionEnd)}`;
            const nextSelection = selectionStart + pastedText.length;

            updateValue(nextValue);
            requestAnimationFrame(() => {
                input.setSelectionRange(nextSelection, nextSelection);
                updateSelection(nextSelection, nextSelection);
            });
        };

        const handleClick: React.MouseEventHandler<HTMLInputElement> = (event) => {
            updateLocalSelection();
            onClick?.(event);
        };

        const handleFocus: React.FocusEventHandler<HTMLInputElement> = (event) => {
            updateLocalSelection();
            onFocus?.(event);
        };

        const handleKeyUp: React.KeyboardEventHandler<HTMLInputElement> = (event) => {
            updateLocalSelection();
            onKeyUp?.(event);
        };

        const handleSelect: React.ReactEventHandler<HTMLInputElement> = (event) => {
            updateLocalSelection();
            onSelect?.(event);
        };

        const handleMouseUp: React.MouseEventHandler<HTMLInputElement> = (event) => {
            updateLocalSelection();
            onMouseUp?.(event);
        };

        const updateLocalSelection = useCallback(() => {
            const input = inputRef.current;
            if (!input) {
                return;
            }

            updateSelection(input.selectionStart ?? 0, input.selectionEnd ?? input.selectionStart ?? 0);
        }, [updateSelection]);

        useLayoutEffect(() => {
            const input = inputRef.current;
            if (!input) {
                return;
            }

            const updateInputType = () => {
                setInputType(input.type);
            };
            const observer = new MutationObserver(updateInputType);

            updateInputType();
            observer.observe(input, {
                attributeFilter: ["type"],
                attributes: true,
            });

            return () => {
                observer.disconnect();
            };
        }, []);

        useLayoutEffect(() => {
            const input = inputRef.current;
            if (!input || !remoteCursors.length) {
                setCursorPositions({});
                return;
            }

            const styles = window.getComputedStyle(input);
            const mirror = document.createElement("div");
            const trackedStyles = [
                "boxSizing",
                "fontFamily",
                "fontSize",
                "fontWeight",
                "letterSpacing",
                "lineHeight",
                "paddingBottom",
                "paddingLeft",
                "paddingRight",
                "paddingTop",
                "textAlign",
            ] as const;

            mirror.style.position = "absolute";
            mirror.style.visibility = "hidden";
            mirror.style.overflow = "hidden";
            mirror.style.top = "0";
            mirror.style.left = "-9999px";
            mirror.style.height = `${input.offsetHeight}px`;
            mirror.style.width = `${input.offsetWidth}px`;
            mirror.style.whiteSpace = "pre";
            trackedStyles.forEach((styleName) => {
                mirror.style[styleName] = styles[styleName];
            });

            const paddingTop = Number.parseFloat(styles.paddingTop) || 0;
            const paddingBottom = Number.parseFloat(styles.paddingBottom) || 0;
            const fontSize = Number.parseFloat(styles.fontSize) || 16;
            const lineHeight = Number.parseFloat(styles.lineHeight) || fontSize * 1.2;
            const contentHeight = input.clientHeight - paddingTop - paddingBottom;
            const top = paddingTop + Math.max((contentHeight - lineHeight) / 2, 0);
            const measureValue = inputType === "password" ? PASSWORD_MASK_CHARACTER.repeat(value.length) : value;

            const nextPositions: Record<number, ICursorOverlayPosition> = {};
            document.body.appendChild(mirror);

            remoteCursors.forEach((cursor) => {
                const selectionStart = Math.min(cursor.selectionStart, cursor.selectionEnd);
                const selectionEnd = Math.max(cursor.selectionStart, cursor.selectionEnd);
                const startMarker = createMeasureMarker();
                const endMarker = createMeasureMarker();

                mirror.textContent = measureValue.slice(0, selectionStart);
                mirror.appendChild(startMarker);
                mirror.appendChild(document.createTextNode(measureValue.slice(selectionStart, selectionEnd)));
                mirror.appendChild(endMarker);
                mirror.appendChild(document.createTextNode(measureValue.slice(selectionEnd)));

                const startLeft = startMarker.offsetLeft - input.scrollLeft;
                const endLeft = endMarker.offsetLeft - input.scrollLeft;
                nextPositions[cursor.clientID] = {
                    caretHeight: lineHeight,
                    caretLeft: endLeft,
                    highlightHeight: lineHeight,
                    highlightLeft: startLeft,
                    highlightWidth: Math.max(endLeft - startLeft, 0),
                    top,
                };
            });

            document.body.removeChild(mirror);
            setCursorPositions(nextPositions);
        }, [inputType, remoteCursors, value]);

        return (
            <div className="relative w-full">
                <Input
                    {...props}
                    ref={composeRefs(ref, inputRef)}
                    disabled={isInputDisabled}
                    readOnly={readOnly}
                    value={value}
                    onChange={handleChange}
                    onClick={handleClick}
                    onFocus={handleFocus}
                    onKeyUp={handleKeyUp}
                    onMouseUp={handleMouseUp}
                    onPaste={handlePaste}
                    onSelect={handleSelect}
                />
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

CollaborativeInput.displayName = "Collaborative.Input";

export default CollaborativeInput;

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
                        {cursor.selectionStart !== cursor.selectionEnd && (
                            <span
                                className="pointer-events-none absolute z-[19] rounded-sm opacity-25"
                                style={{
                                    backgroundColor: cursor.color,
                                    height: position.highlightHeight,
                                    left: position.highlightLeft,
                                    top: position.top,
                                    width: position.highlightWidth,
                                }}
                            />
                        )}
                        <span
                            className="pointer-events-none absolute z-[20] w-0.5"
                            style={{
                                backgroundColor: cursor.color,
                                height: position.caretHeight,
                                left: position.caretLeft,
                                top: position.top,
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
