import { useRef, type ReactNode } from "react";
import type { PlateEditor } from "platejs/react";
import { EEditorType } from "@langboard/core/constants";
import { useTranslation } from "react-i18next";
import { useMarkdownSourceMode } from "./useMarkdownSourceMode";
import { MarkdownSourceSurface } from "./MarkdownSourceSurface";

interface Props {
    editor: PlateEditor;
    userUID: string;
    cardUID?: string;
    editorType: string;
    initialSource?: string;
    readOnly?: boolean;
    disabled: boolean;
    onSourceChange: () => void;
    children: (split: boolean) => ReactNode;
}

export function DescriptionMarkdownEditor({
    editor,
    userUID,
    cardUID,
    editorType,
    initialSource,
    readOnly,
    disabled,
    onSourceChange,
    children,
}: Props) {
    const [t] = useTranslation();
    const root = useRef<HTMLDivElement>(null);
    const mode = useMarkdownSourceMode(userUID, !readOnly && editorType === EEditorType.CardDescription);
    if (!mode.available) return children(false);
    const buttonClass = [
        "rounded px-3 py-1 text-sm transition-colors duration-100 hover:bg-accent",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
    ].join(" ");
    return (
        <div ref={root}>
            <div className="border-b px-2 py-1 text-right">
                {mode.enabled && (
                    <button type="button" className={buttonClass} onClick={mode.swap}>
                        {t("editor.Swap panes")}
                    </button>
                )}
                <button
                    type="button"
                    aria-pressed={mode.enabled}
                    className={buttonClass}
                    onClick={() => {
                        mode.toggle();
                        requestAnimationFrame(() => {
                            const input = root.current?.querySelector("textarea");
                            if (input) input.focus();
                            else editor.tf.focus();
                        });
                    }}
                >
                    {t("editor.Markdown split")}
                </button>
            </div>
            {mode.enabled ? (
                <MarkdownSourceSurface
                    editor={editor}
                    initialSource={initialSource}
                    disabled={disabled}
                    previewFirst={mode.previewFirst}
                    draftKey={`langboard:source-recovery:${userUID}:${cardUID ?? ""}`}
                    onSourceChange={onSourceChange}
                >
                    {children(true)}
                </MarkdownSourceSurface>
            ) : (
                children(false)
            )}
        </div>
    );
}
