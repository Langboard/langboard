/* eslint-disable @/max-len */
"use client";

import * as React from "react";
import { AIChatPlugin, AIPlugin, useEditorChat, useLastAssistantMessage } from "@platejs/ai/react";
import { BlockSelectionPlugin, useIsSelecting } from "@platejs/selection/react";
import { Command as CommandPrimitive } from "cmdk";
import {
    Album,
    BadgeHelp,
    Check,
    CornerUpLeft,
    FeatherIcon,
    ListEnd,
    ListMinus,
    ListPlus,
    Loader2Icon,
    PauseIcon,
    PenLine,
    SmileIcon,
    Wand,
    X,
} from "lucide-react";
import { type NodeEntry, type SlateEditor, isHotkey, NodeApi } from "platejs";
import { useEditorPlugin, useFocusedLast, useHotkeys, usePluginOption } from "platejs/react";
import { type PlateEditor, useEditorRef } from "platejs/react";
import { cn } from "@/core/utils/ComponentUtils";
import Button from "@/components/base/Button";
import Command from "@/components/base/Command";
import Popover from "@/components/base/Popover";
import { AIChatEditor } from "@/components/plate-ui/ai-chat-editor";
import { useTranslation } from "react-i18next";
import { EDITOR_CHAT_KEY, TAIChatOption } from "@/components/Editor/useChat";

export function AIMenu() {
    const [t] = useTranslation();
    const { api, editor } = useEditorPlugin(AIChatPlugin);
    const mode = usePluginOption(AIChatPlugin, "mode");
    const toolName = usePluginOption(AIChatPlugin, "toolName");
    const streaming = usePluginOption(AIChatPlugin, "streaming");
    const isSelecting = useIsSelecting();
    const isFocusedLast = useFocusedLast();
    const open = usePluginOption(AIChatPlugin, "open") && isFocusedLast;
    const [value, setValue] = React.useState("");
    const [input, setInput] = React.useState("");

    const chat = usePluginOption(AIChatPlugin, EDITOR_CHAT_KEY) as TAIChatOption | undefined;
    const messages = chat?.messages ?? [];
    const status = chat?.status ?? "ready";
    const [anchorElement, setAnchorElement] = React.useState<HTMLElement | null>(null);

    const content = useLastAssistantMessage()?.parts.find((part) => part.type === "text")?.text;

    React.useEffect(() => {
        if (streaming) {
            const anchor = api.aiChat.node({ anchor: true });
            setTimeout(() => {
                const anchorDom = editor.api.toDOMNode(anchor![0])!;
                setAnchorElement(anchorDom);
            }, 0);
        }
    }, [streaming]);

    const setOpen = (open: bool) => {
        if (open) {
            api.aiChat.show();
        } else {
            api.aiChat.hide();
        }
    };

    const show = (anchorElement: HTMLElement) => {
        setAnchorElement(anchorElement);
        setOpen(true);
    };

    useEditorChat({
        onOpenBlockSelection: (blocks: NodeEntry[]) => {
            show(editor.api.toDOMNode(blocks.at(-1)![0])!);
        },
        onOpenChange: (open) => {
            if (!open) {
                setAnchorElement(null);
                setInput("");
            }
        },
        onOpenCursor: () => {
            const [ancestor] = editor.api.block({ highest: true })!;

            if (!editor.api.isAt({ end: true }) && !editor.api.isEmpty(ancestor)) {
                editor.getApi(BlockSelectionPlugin).blockSelection.set(ancestor.id as string);
            }

            show(editor.api.toDOMNode(ancestor)!);
        },
        onOpenSelection: () => {
            show(editor.api.toDOMNode(editor.api.blocks().at(-1)![0])!);
        },
    });

    useHotkeys("esc", () => {
        api.aiChat.stop();

        chat?.abort?.();
    });

    const isLoading = status === "streaming" || status === "submitted";

    if (isLoading && mode === "insert") {
        return null;
    }

    if (toolName === "comment") {
        return null;
    }

    if (toolName === "edit" && mode === "chat" && isLoading) {
        return null;
    }

    return (
        <Popover.Root open={open} onOpenChange={setOpen} modal={false}>
            <Popover.Anchor virtualRef={{ current: anchorElement! }} />

            <Popover.Content
                className="border-none bg-transparent p-0 shadow-none"
                style={{
                    width: anchorElement?.offsetWidth,
                }}
                onEscapeKeyDown={(e) => {
                    e.preventDefault();

                    api.aiChat.hide();
                }}
                align="center"
                side="bottom"
            >
                <Command.Root className="w-full rounded-lg border shadow-md" value={value} onValueChange={setValue}>
                    {mode === "chat" && isSelecting && content && <AIChatEditor content={content} />}

                    {isLoading ? (
                        <div className="flex grow select-none items-center gap-2 p-2 text-sm text-muted-foreground">
                            <Loader2Icon className="size-4 animate-spin" />
                            {chat?.graphProgress ? (
                                <span className="badge badge-sm" role="status">
                                    {t(`bot.graph_progress.${chat.graphProgress}`)}
                                </span>
                            ) : (
                                t(`editor.${messages.length > 1 ? "Editing..." : "Thinking..."}`)
                            )}
                        </div>
                    ) : (
                        <CommandPrimitive.Input
                            className={cn(
                                "flex h-9 w-full min-w-0 border-input bg-transparent px-3 py-1 text-base outline-none transition-[color,box-shadow] placeholder:text-muted-foreground dark:bg-input/30 md:text-sm",
                                "aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40",
                                "border-b focus-visible:ring-transparent"
                            )}
                            value={input}
                            onKeyDown={(e) => {
                                if (isHotkey("backspace")(e) && input.length === 0) {
                                    e.preventDefault();
                                    api.aiChat.hide();
                                }
                                if (isHotkey("enter")(e) && !e.shiftKey && !value) {
                                    e.preventDefault();
                                    void api.aiChat.submit(input);
                                    setInput("");
                                }
                            }}
                            onValueChange={setInput}
                            placeholder={t("editor.Ask AI anything...")}
                            data-plate-focus
                            autoFocus
                        />
                    )}

                    {!isLoading && (
                        <Command.List>
                            <AIMenuItems input={input} setInput={setInput} setValue={setValue} />
                        </Command.List>
                    )}
                </Command.Root>
            </Popover.Content>
        </Popover.Root>
    );
}

type EditorChatState = "cursorCommand" | "cursorSuggestion" | "selectionCommand" | "selectionSuggestion";

const aiChatItems = {
    accept: {
        icon: <Check className="size-4" />,
        label: "editor.Accept",
        value: "accept",
        onSelect: ({ aiEditor, editor }) => {
            const { mode, toolName } = editor.getOptions(AIChatPlugin);

            if (mode === "chat" && toolName === "generate") {
                return editor.getTransforms(AIChatPlugin).aiChat.replaceSelection(aiEditor);
            }

            editor.getTransforms(AIChatPlugin).aiChat.accept();
            editor.tf.focus({ edge: "end" });
        },
    },
    continueWrite: {
        icon: <PenLine className="size-4" />,
        label: "editor.Continue writing",
        value: "continueWrite",
        onSelect: ({ editor, input }) => {
            const ancestorNode = editor.api.block({ highest: true });

            if (!ancestorNode) return;

            const isEmpty = NodeApi.string(ancestorNode[0]).trim().length === 0;

            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                mode: "insert",
                prompt: isEmpty
                    ? `<Document>
{editor}
</Document>
Start writing a new paragraph AFTER <Document> ONLY ONE SENTENCE`
                    : "Continue writing AFTER <Block> ONLY ONE SENTENCE. DONT REPEAT THE TEXT.",
                toolName: "generate",
            });
        },
    },
    discard: {
        icon: <X className="size-4" />,
        label: "editor.Discard",
        shortcut: "Escape",
        value: "discard",
        onSelect: ({ editor }) => {
            editor.getTransforms(AIPlugin).ai.undo();
            editor.getApi(AIChatPlugin).aiChat.hide();
        },
    },
    emojify: {
        icon: <SmileIcon className="size-4" />,
        label: "editor.Emojify",
        value: "emojify",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Add a small number of contextually relevant emojis within each block only. You may insert emojis, but do not remove, replace, or rewrite existing text, and do not modify Markdown syntax, links, or line breaks.",
                toolName: "edit",
            });
        },
    },
    explain: {
        icon: <BadgeHelp className="size-4" />,
        label: "editor.Explain",
        value: "explain",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: {
                    default: "Explain {editor}",
                    selecting: "Explain",
                },
                toolName: "generate",
            });
        },
    },
    fixSpelling: {
        icon: <Check className="size-4" />,
        label: "editor.Fix spelling & grammar",
        value: "fixSpelling",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Fix spelling, grammar, and punctuation errors within each block only, without changing meaning, tone, or adding new information.",
                toolName: "edit",
            });
        },
    },
    improveWriting: {
        icon: <Wand className="size-4" />,
        label: "editor.Improve writing",
        value: "improveWriting",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Improve the writing for clarity and flow, without changing meaning or adding new information.",
                toolName: "edit",
            });
        },
    },
    insertBelow: {
        icon: <ListEnd className="size-4" />,
        label: "editor.Insert below",
        value: "insertBelow",
        onSelect: ({ aiEditor, editor }) => {
            /** Format: 'none' Fix insert table */
            void editor.getTransforms(AIChatPlugin).aiChat.insertBelow(aiEditor, { format: "none" });
        },
    },
    makeLonger: {
        icon: <ListPlus className="size-4" />,
        label: "editor.Make longer",
        value: "makeLonger",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Make the content longer by elaborating on existing ideas within each block only, without changing meaning or adding new information.",
                toolName: "edit",
            });
        },
    },
    makeShorter: {
        icon: <ListMinus className="size-4" />,
        label: "editor.Make shorter",
        value: "makeShorter",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Make the content shorter by reducing verbosity within each block only, without changing meaning or removing essential information.",
                toolName: "edit",
            });
        },
    },
    replace: {
        icon: <Check className="size-4" />,
        label: "editor.Replace selection",
        value: "replace",
        onSelect: ({ aiEditor, editor }) => {
            void editor.getTransforms(AIChatPlugin).aiChat.replaceSelection(aiEditor);
        },
    },
    simplifyLanguage: {
        icon: <FeatherIcon className="size-4" />,
        label: "editor.Simplify language",
        value: "simplifyLanguage",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                prompt: "Simplify the language by using clearer and more straightforward wording within each block only, without changing meaning or adding new information.",
                toolName: "edit",
            });
        },
    },
    summarize: {
        icon: <Album className="size-4" />,
        label: "editor.Add a summary",
        value: "summarize",
        onSelect: ({ editor, input }) => {
            void editor.getApi(AIChatPlugin).aiChat.submit(input, {
                mode: "insert",
                prompt: {
                    default: "Summarize {editor}",
                    selecting: "Summarize",
                },
                toolName: "generate",
            });
        },
    },
    tryAgain: {
        icon: <CornerUpLeft className="size-4" />,
        label: "editor.Try again",
        value: "tryAgain",
        onSelect: ({ editor }) => {
            void editor.getApi(AIChatPlugin).aiChat.reload();
        },
    },
} satisfies Record<
    string,
    {
        icon: React.ReactNode;
        label: string;
        value: string;
        component?: React.ComponentType<{ menuState: EditorChatState }>;
        filterItems?: bool;
        items?: { label: string; value: string }[];
        shortcut?: string;
        onSelect?: ({ aiEditor, editor, input }: { aiEditor: SlateEditor; editor: PlateEditor; input: string }) => void;
    }
>;

const menuStateItems: Record<
    EditorChatState,
    {
        items: (typeof aiChatItems)[keyof typeof aiChatItems][];
        heading?: string;
    }[]
> = {
    cursorCommand: [
        {
            items: [aiChatItems.continueWrite, aiChatItems.summarize, aiChatItems.explain],
        },
    ],
    cursorSuggestion: [
        {
            items: [aiChatItems.accept, aiChatItems.discard, aiChatItems.tryAgain],
        },
    ],
    selectionCommand: [
        {
            items: [
                aiChatItems.improveWriting,
                aiChatItems.emojify,
                aiChatItems.makeLonger,
                aiChatItems.makeShorter,
                aiChatItems.fixSpelling,
                aiChatItems.simplifyLanguage,
            ],
        },
    ],
    selectionSuggestion: [
        {
            items: [aiChatItems.accept, aiChatItems.discard, aiChatItems.insertBelow, aiChatItems.tryAgain],
        },
    ],
};

export const AIMenuItems = ({
    input,
    setInput,
    setValue,
}: {
    input: string;
    setInput: (value: string) => void;
    setValue: (value: string) => void;
}) => {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const { messages } = usePluginOption(AIChatPlugin, EDITOR_CHAT_KEY);
    const aiEditor = usePluginOption(AIChatPlugin, "aiEditor")!;
    const isSelecting = useIsSelecting();

    const menuState = React.useMemo(() => {
        if (messages && messages.length > 0) {
            return isSelecting ? "selectionSuggestion" : "cursorSuggestion";
        }

        return isSelecting ? "selectionCommand" : "cursorCommand";
    }, [isSelecting, messages]);

    const menuGroups = React.useMemo(() => {
        const items = menuStateItems[menuState];

        return items;
    }, [menuState]);

    React.useEffect(() => {
        if (menuGroups.length > 0 && menuGroups[0].items.length > 0) {
            setValue(menuGroups[0].items[0].value);
        }
    }, [menuGroups, setValue]);

    return (
        <>
            {menuGroups.map((group, index) => (
                <Command.Group key={index} heading={group.heading}>
                    {group.items.map((menuItem) => (
                        <Command.Item
                            key={menuItem.value}
                            className="[&_svg]:text-muted-foreground"
                            value={menuItem.value}
                            onSelect={() => {
                                menuItem.onSelect?.({
                                    aiEditor,
                                    editor,
                                    input,
                                });
                                setInput("");
                            }}
                        >
                            {menuItem.icon}
                            <span className="ml-1">{t(menuItem.label)}</span>
                        </Command.Item>
                    ))}
                </Command.Group>
            ))}
        </>
    );
};

export function AILoadingBar() {
    const [t] = useTranslation();
    const toolName = usePluginOption(AIChatPlugin, "toolName");
    const chat = usePluginOption(AIChatPlugin, EDITOR_CHAT_KEY) as TAIChatOption;
    const mode = usePluginOption(AIChatPlugin, "mode");

    const { status } = chat;

    const { api } = useEditorPlugin(AIChatPlugin);

    const isLoading = status === "streaming" || status === "submitted";

    const handleComments = (_: "accept" | "reject") => {
        api.aiChat.hide();
    };

    useHotkeys("esc", () => {
        api.aiChat.stop();
    });

    if (isLoading && (mode === "insert" || toolName === "comment" || (toolName === "edit" && mode === "chat"))) {
        return (
            <div
                className={cn(
                    "absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-md border border-border bg-muted px-3 py-1.5 text-sm text-muted-foreground shadow-md transition-all duration-300"
                )}
            >
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
                <span className={chat.graphProgress ? "badge badge-sm" : undefined} role="status">
                    {chat.graphProgress
                        ? t(`bot.graph_progress.${chat.graphProgress}`)
                        : status === "submitted"
                          ? t("editor.Thinking")
                          : t("editor.Writing")}
                </span>
                <Button size="sm" variant="ghost" className="flex items-center gap-1 text-xs" onClick={() => api.aiChat.stop()}>
                    <PauseIcon className="h-4 w-4" />
                    {t("editor.Stop")}
                    <kbd className="ml-1 rounded bg-border px-1 font-mono text-[10px] text-muted-foreground shadow-sm">{t("editor.Esc")}</kbd>
                </Button>
            </div>
        );
    }

    if (toolName === "comment" && status === "ready") {
        return (
            <div
                className={cn(
                    "absolute bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-0 rounded-xl border border-border/50 bg-popover p-1 text-sm text-muted-foreground shadow-xl backdrop-blur-sm",
                    "p-3"
                )}
            >
                {/* Header with controls */}
                <div className="flex w-full items-center justify-between gap-3">
                    <div className="flex items-center gap-5">
                        <Button size="sm" disabled={isLoading} onClick={() => handleComments("accept")}>
                            {t("editor.Accept")}
                        </Button>

                        <Button size="sm" disabled={isLoading} onClick={() => handleComments("reject")}>
                            {t("editor.Reject")}
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return null;
}
