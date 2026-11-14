/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import { withAIBatch } from "@platejs/ai";
import { AIChatPlugin, AIPlugin, useChatChunk, streamInsertChunk, applyAISuggestions } from "@platejs/ai/react";
import { getPluginType, KEYS, PathApi } from "platejs";
import { usePluginOption } from "platejs/react";
import { useEffect, useMemo } from "react";
import { AILoadingBar, AIMenu } from "@/components/plate-ui/ai-menu";
import { AIAnchorElement, AILeaf } from "@/components/plate-ui/ai-node";
import { CursorOverlayKit } from "@/components/Editor/plugins/cursor-overlay-kit";
import { MarkdownKit } from "@/components/Editor/plugins/markdown-kit";
import { EDITOR_CHAT_KEY, IUseChat, TAIChatOption, useChat } from "@/components/Editor/useChat";

export interface ICreateAIKit extends IUseChat {}

export const createAiKit = ({ socket, eventKey, events, commonEventData }: ICreateAIKit) => {
    return [
        ...CursorOverlayKit,
        ...MarkdownKit,
        AIPlugin.withComponent(AILeaf),
        AIChatPlugin.extend({
            render: {
                afterContainer: AILoadingBar,
                afterEditable: AIMenu,
                node: AIAnchorElement,
            },
            shortcuts: { show: { keys: "mod+j" } },
            useHooks: ({ editor, getOption }) => {
                const mode = usePluginOption(AIChatPlugin, "mode");
                const toolName = usePluginOption(AIChatPlugin, "toolName");
                const chat = useChat({ socket, eventKey, events, commonEventData });
                const chatOption = useMemo(
                    () =>
                        ({
                            abort: chat.abort,
                            messages: chat.messages,
                            regenerate: chat.regenerate,
                            sendMessage: chat.sendMessage,
                            setMessages: chat.setMessages,
                            status: chat.status,
                            graphProgress: chat.graphProgress,
                            stop: chat.stop,
                        }) as TAIChatOption,
                    [chat.messages, chat.status, chat.graphProgress]
                );

                useEffect(() => {
                    const currentChat = editor.getOptions(AIChatPlugin).chat;
                    if (
                        currentChat?.messages === chatOption.messages &&
                        currentChat?.status === chatOption.status &&
                        (currentChat as TAIChatOption)?.graphProgress === chatOption.graphProgress
                    ) {
                        return;
                    }

                    editor.setOption(AIChatPlugin, EDITOR_CHAT_KEY, chatOption as any);
                }, [chatOption, editor]);

                useChatChunk({
                    onChunk: ({ chunk, isFirst, nodes, text: content }) => {
                        if (isFirst && mode === "insert") {
                            const selectionPath = editor.selection?.focus.path;
                            const anchorEntry = editor.getApi(AIChatPlugin).aiChat.node({ anchor: true });
                            const lastBlock = editor.api.blocks({ mode: "highest" }).at(-1);

                            const insertPath = selectionPath
                                ? PathApi.next(selectionPath.slice(0, 1))
                                : anchorEntry
                                  ? PathApi.next(anchorEntry[1].slice(0, 1))
                                  : lastBlock
                                    ? PathApi.next(lastBlock[1].slice(0, 1))
                                    : [0];

                            editor.tf.withoutSaving(() => {
                                editor.tf.insertNodes(
                                    {
                                        children: [{ text: "" }],
                                        type: getPluginType(editor, KEYS.aiChat),
                                    },
                                    {
                                        at: insertPath,
                                    }
                                );
                            });
                            editor.setOption(AIChatPlugin, "streaming", true);
                        }

                        if (mode === "insert" && nodes.length > 0) {
                            withAIBatch(
                                editor,
                                () => {
                                    if (!getOption("streaming")) return;
                                    editor.tf.withScrolling(() => {
                                        streamInsertChunk(editor, chunk, {
                                            textProps: {
                                                [getPluginType(editor, KEYS.ai)]: true,
                                            },
                                        });
                                    });
                                },
                                { split: isFirst }
                            );
                        }

                        if (toolName === "edit" && mode === "chat") {
                            withAIBatch(
                                editor,
                                () => {
                                    applyAISuggestions(editor, content);
                                },
                                {
                                    split: isFirst,
                                }
                            );
                        }
                    },
                    onFinish: () => {
                        editor.setOption(AIChatPlugin, "streaming", false);
                        editor.setOption(AIChatPlugin, "_blockChunks", "");
                        editor.setOption(AIChatPlugin, "_blockPath", null);
                        editor.setOption(AIChatPlugin, "_mdxName", null);
                    },
                });
            },
        }),
    ];
};
