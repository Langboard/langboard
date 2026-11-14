"use client";

import { useEffect } from "react";
import type { TEditor } from "@/components/Editor/editor-kit";
import { KEYS } from "platejs";
import { useEditorReadOnly } from "platejs/react";
import { BoldIcon, Code2Icon, ItalicIcon, StrikethroughIcon, UnderlineIcon, WandSparklesIcon } from "lucide-react";
import { AIToolbarButton } from "@/components/plate-ui/ai-toolbar-button";
import { LinkToolbarButton } from "@/components/plate-ui/link-toolbar-button";
import { MarkToolbarButton } from "@/components/plate-ui/mark-toolbar-button";
import { MoreToolbarButton } from "@/components/plate-ui/more-toolbar-button";
import { ToolbarGroup } from "@/components/plate-ui/toolbar";
import { TurnIntoToolbarButton } from "@/components/plate-ui/turn-into-toolbar-button";
import { useEditorData } from "@/core/providers/EditorDataProvider";
import { ToolbarButton } from "@/components/plate-ui/toolbar";
import { useEditorSelector } from "platejs/react";
import { useTranslation } from "react-i18next";

export function FloatingToolbarButtons() {
    const [t] = useTranslation();
    const readOnly = useEditorReadOnly();
    const { wikiSecretSelection } = useEditorData();
    const selectionKey = useEditorSelector((editor) => JSON.stringify(editor.selection), []);
    useEffect(() => {
        wikiSecretSelection?.selectionChanged?.();
    }, [selectionKey, wikiSecretSelection?.selectionChanged]);
    const hasSelection = useEditorSelector(
        (editor) =>
            !!editor.selection &&
            JSON.stringify(editor.selection.anchor) !== JSON.stringify(editor.selection.focus) &&
            (wikiSecretSelection?.isSupported(editor as TEditor) ?? false),
        [wikiSecretSelection]
    );

    return (
        <>
            {!readOnly && (
                <>
                    {wikiSecretSelection && (
                        <ToolbarGroup>
                            <ToolbarButton
                                disabled={wikiSecretSelection.disabled || !hasSelection}
                                tooltip={t("wiki.secretSelection.help")}
                                onMouseDown={(event) => event.preventDefault()}
                                onClick={() => {
                                    const name = window.prompt(t("wiki.secretSelection.name"));
                                    if (name?.trim()) void wikiSecretSelection.convert(name.trim());
                                }}
                            >
                                {t("wiki.secretSelection.convert")}
                            </ToolbarButton>
                        </ToolbarGroup>
                    )}
                    <ToolbarGroup>
                        <AIToolbarButton tooltip={t("editor.AI commands")}>
                            <WandSparklesIcon />
                            {t("editor.Ask AI")}
                        </AIToolbarButton>
                    </ToolbarGroup>

                    <ToolbarGroup>
                        <TurnIntoToolbarButton />

                        <MarkToolbarButton nodeType={KEYS.bold} tooltip={t("editor.Bold (⌘+B)")}>
                            <BoldIcon />
                        </MarkToolbarButton>

                        <MarkToolbarButton nodeType={KEYS.italic} tooltip={t("editor.Italic (⌘+I)")}>
                            <ItalicIcon />
                        </MarkToolbarButton>

                        <MarkToolbarButton nodeType={KEYS.underline} tooltip={t("editor.Underline (⌘+U)")}>
                            <UnderlineIcon />
                        </MarkToolbarButton>

                        <MarkToolbarButton nodeType={KEYS.strikethrough} tooltip={t("editor.Strikethrough (⌘+⇧+M)")}>
                            <StrikethroughIcon />
                        </MarkToolbarButton>

                        <MarkToolbarButton nodeType={KEYS.code} tooltip={t("editor.Code (⌘+E)")}>
                            <Code2Icon />
                        </MarkToolbarButton>

                        <LinkToolbarButton />
                    </ToolbarGroup>
                </>
            )}

            <ToolbarGroup>{!readOnly && <MoreToolbarButton />}</ToolbarGroup>
        </>
    );
}
