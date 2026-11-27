/* eslint-disable @/max-len */
"use client";

import type { TElement } from "platejs";
import { CopilotPlugin } from "@platejs/ai/react";
import { serializeMd, stripMarkdown } from "@platejs/markdown";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus, ESocketTopic } from "@langboard/core/enums";
import { GhostText } from "@/components/plate-ui/ghost-text";
import { IUseChat } from "@/components/Editor/useChat";
import { MarkdownKit } from "@/components/Editor/plugins/markdown-kit";
import { ISocketEvent } from "@/core/stores/socket/types";

export interface ICreateCopilotKit extends Omit<IUseChat, "events"> {
    events: {
        abort: string;
        send: string;
        receive: string;
    };
}

export const createCopilotKit = ({ socket, eventKey, events, commonEventData }: ICreateCopilotKit) => {
    return [
        ...MarkdownKit,
        CopilotPlugin.configure(({ api }) => ({
            options: {
                completeOptions: {
                    fetch: async (_, init) => {
                        const badResponse = new Response(null, {
                            status: EHttpStatus.HTTP_400_BAD_REQUEST,
                        });

                        if (!Utils.Type.isString(init?.body)) {
                            return badResponse;
                        }

                        const body = JSON.parse(init.body);
                        const key = Utils.String.Token.generate(8);
                        const receiveEventWithKey = `${events.receive}:${key}`;
                        const copilotEventKey = `plate-copilot-${eventKey}-${key}`;

                        const waitResponse = new Promise((resolve) => {
                            const receive = (data: { text: string }) => {
                                socket.off({
                                    topic: ESocketTopic.None,
                                    event: receiveEventWithKey,
                                    eventKey: copilotEventKey,
                                    callback: receive as ISocketEvent<unknown>,
                                });
                                if (init.signal) {
                                    init.signal.onabort = null;
                                }
                                resolve(data);
                            };

                            if (init.signal) {
                                init.signal.onabort = () => {
                                    socket.send({
                                        topic: ESocketTopic.None,
                                        eventName: events.abort,
                                        data: { task_id: key },
                                    });
                                    receive({ text: "0" });
                                };
                            }

                            if (!init.signal?.aborted) {
                                socket.on({
                                    topic: ESocketTopic.None,
                                    eventKey: copilotEventKey,
                                    event: receiveEventWithKey,
                                    callback: receive,
                                });
                                socket.send({
                                    topic: ESocketTopic.None,
                                    eventName: events.send,
                                    data: {
                                        ...body,
                                        ...(commonEventData ?? {}),
                                        task_id: key,
                                    },
                                });
                            }
                        });

                        const result = await waitResponse;
                        return new Response(JSON.stringify(result), {
                            headers: {
                                "Content-Type": "application/json",
                            },
                        });
                    },
                    body: {
                        system: `You are an advanced AI writing assistant, similar to VSCode Copilot but for general text. Your task is to predict and generate the next part of the text based on the given context.

  Rules:
  - Continue the text naturally up to the next punctuation mark (., ,, ;, :, ?, or !).
  - Maintain style and tone. Don't repeat given text.
  - For unclear context, provide the most likely continuation.
  - Handle code snippets, lists, or structured text if needed.
  - Don't include """ in your response.
  - CRITICAL: Always end with a punctuation mark.
  - CRITICAL: Avoid starting a new block. Do not use block formatting like >, #, 1., 2., -, etc. The suggestion should continue in the same block as the context.
  - If no context is provided or you can't generate a continuation, return "0" without explanation.`,
                    },
                    onError: () => {
                        return;
                    },
                    onFinish: (_, completion) => {
                        if (completion === "0") return;

                        api.copilot.setBlockSuggestion({
                            text: stripMarkdown(completion),
                        });
                    },
                },
                debounceDelay: 300,
                renderGhostText: GhostText,
                getPrompt: ({ editor }) => {
                    const contextEntry = editor.api.block({ highest: true });

                    if (!contextEntry) return "";

                    const prompt = serializeMd(editor, {
                        value: [contextEntry[0] as TElement],
                    });

                    return `Continue the text up to the next punctuation mark:
  """
  ${prompt}
  """`;
                },
            },
            shortcuts: {
                accept: {
                    keys: "tab",
                },
                acceptNextWord: {
                    keys: "mod+right",
                },
                reject: {
                    keys: "escape",
                },
                triggerSuggestion: {
                    keys: "ctrl+space",
                },
            },
        })),
    ];
};
