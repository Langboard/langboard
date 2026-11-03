import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { runInNewContext } from "node:vm";
import { Chat } from "@ai-sdk/react";
import type { ChatInit, UIMessage } from "ai";
import i18next from "i18next";
import ts from "typescript";
import type { IUseChat } from "./useChat";

const locales = ["en-US", "ko-KR", "ja-JP", "zh-CN"];
const resources = Object.fromEntries(
    locales.map((locale) => [
        locale,
        { translation: { errors: JSON.parse(readFileSync(new URL(`../../assets/locales/${locale}/errors.json`, import.meta.url), "utf8")) } },
    ])
);
type Callback = (data: Record<string, unknown>) => void;
const createHarness = async (locale = "en-US") => {
    const i18n = i18next.createInstance();
    await i18n.init({ lng: locale, resources });
    const toasts: string[] = [];
    const progress: unknown[] = [];
    const storage = new Map<string, string>();
    const timers = new Map<number, () => void>();
    const listeners = new Map<string, { event: string; callback: Callback }>();
    const requests: { eventName: string; data: Record<string, unknown> }[] = [];
    let timerID = 0;
    let connected = true;
    let callbacks: Record<string, Callback> | undefined;
    let chat: Chat<UIMessage>;
    const socket = {
        send: (request: (typeof requests)[number]) => {
            requests.push(request);
            return { isConnected: connected };
        },
        stream: (props: { callbacks: Record<string, Callback> }) => {
            callbacks = props.callbacks;
        },
        streamOff: () => {
            callbacks = undefined;
        },
        on: (props: { eventKey: string; event: string; callback: Callback }) => listeners.set(`${props.eventKey}:${props.event}`, props),
        off: (props: { eventKey: string; event: string }) => listeners.delete(`${props.eventKey}:${props.event}`),
    };
    const require = createRequire(import.meta.url);
    const modules = new Map<string, unknown>();
    const load = (path: string): unknown => {
        if (modules.has(path)) return modules.get(path);
        const module = { exports: {} };
        const javascript = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
        }).outputText;
        runInNewContext(javascript, {
            module,
            exports: module.exports,
            ReadableStream,
            window: {
                sessionStorage: {
                    getItem: (key: string) => storage.get(key) ?? null,
                    setItem: (key: string, value: string) => storage.set(key, value),
                    removeItem: (key: string) => storage.delete(key),
                },
            },
            setTimeout: (callback: () => void) => {
                timers.set(++timerID, callback);
                return timerID;
            },
            clearTimeout: (id: number) => timers.delete(id),
            require: (name: string) => {
                if (name === "react")
                    return {
                        useState: () => [undefined, (value: unknown) => progress.push(value)],
                        useRef: (current: unknown) => ({ current }),
                        useMemo: (factory: () => unknown) => factory(),
                        useCallback: (callback: unknown) => callback,
                    };
                if (name === "@ai-sdk/react")
                    return {
                        useChat: (options: ChatInit<UIMessage>) => {
                            chat = new Chat(options);
                            return chat;
                        },
                    };
                if (name === "i18next") return { t: i18n.t.bind(i18n) };
                if (name === "@/components/base/Toast") return { Add: { error: (message: string) => toasts.push(message) } };
                if (name === "@langboard/core/utils")
                    return {
                        Utils: { Type: { isString: (value: unknown) => typeof value === "string" }, String: { Token: { uuid: () => "task-1" } } },
                    };
                if (name === "@langboard/core/enums") return { ESocketTopic: { None: "none", Global: "global" } };
                if (name === "@langboard/core/constants") return { SocketEvents: { SERVER: { GLOBALS: { TASK_ABORTED: "aborted" } } } };
                if (name === "@/controllers/socket/shared/EditorAIRunCancellation")
                    return load("../../controllers/socket/shared/EditorAIRunCancellation.ts");
                if (name === "@/core/constants/InternalBotRun") return load("../../core/constants/InternalBotRun.ts");
                return require(name);
            },
        });
        modules.set(path, module.exports);
        return module.exports;
    };
    const module = load("./useChat.tsx") as typeof import("./useChat");
    const result = module.useChat({
        socket: socket as unknown as IUseChat["socket"],
        eventKey: "card-1",
        events: { send: "send", stream: "stream", abort: "abort", status: "status", statusResult: "statusResult" },
        commonEventData: { project_uid: "project-1" },
    });
    return {
        chat: chat!,
        abort: result.abort,
        i18n,
        storage,
        toasts,
        progress,
        requests,
        timers,
        listeners,
        emit: (event: string, data: Record<string, unknown> = {}) => {
            for (const listener of [...listeners.values()]) if (listener.event === event) listener.callback(data);
        },
        flush: () => new Promise<void>((resolve) => setImmediate(resolve)),
        receive: (event: string, data: Record<string, unknown> = {}) => callbacks?.[event](data),
        disconnect: () => {
            connected = false;
        },
        tick: () => {
            const entry = timers.entries().next().value;
            assert.ok(entry);
            timers.delete(entry[0]);
            entry[1]();
        },
        hasStream: () => callbacks !== undefined,
    };
};

for (const locale of locales) {
    test(`editor failures reach localized toast through SDK transport in ${locale}`, async (context) => {
        for (const failure of ["disconnected", "stream", "failed", "cancelled", "uncertain", "error", "timeout"]) {
            await context.test(failure, async () => {
                const h = await createHarness(locale);
                const privateDiagnostic = "Private provider failure: token=secret";
                if (failure === "disconnected") h.disconnect();
                const sent = h.chat.sendMessage({ text: "User text remains unchanged" });
                await h.flush();
                if (failure === "stream") h.receive("end", { status: "failed", message: privateDiagnostic });
                else if (failure === "timeout") {
                    h.receive("error");
                    while (h.timers.size) h.tick();
                } else if (failure !== "disconnected") {
                    h.receive("error");
                    h.emit("statusResult", { task_id: "task-1", status: failure, error_message: privateDiagnostic });
                }
                await sent;
                assert.equal(h.chat.status, "error");
                assert.ok(h.chat.error);
                if (!["disconnected", "timeout"].includes(failure)) assert.equal(h.chat.error.message, privateDiagnostic);
                assert.deepEqual(h.toasts, [resources[locale].translation.errors["Internal server error"]]);
                assert.ok(!h.toasts.some((message) => message.includes(privateDiagnostic)));
                assert.equal((h.chat.messages[0].parts[0] as { text: string }).text, "User text remains unchanged");
                assert.equal(h.hasStream(), false);
                assert.equal(h.listeners.size, 0);
                assert.equal(h.timers.size, 0);
                assert.equal(h.storage.get("langboard:editor-ai-run:card-1"), failure === "timeout" ? "task-1" : undefined);
            });
        }
    });
}

test("an existing transport translates errors using the locale active at failure", async () => {
    const h = await createHarness();
    const sent = h.chat.sendMessage({ text: "Hello" });
    await h.flush();
    await h.i18n.changeLanguage("ko-KR");
    h.receive("end", { status: "failed", message: "Private diagnostic" });
    await sent;
    assert.deepEqual(h.toasts, [resources["ko-KR"].translation.errors["Internal server error"]]);
});

test("successful streaming preserves model text and suppresses failure guidance", async () => {
    const h = await createHarness();
    const sent = h.chat.sendMessage({ text: "Hello" });
    await h.flush();
    h.receive("buffer", { message: "Model content: " });
    h.receive("end", { message: "Model content: 원문 그대로" });
    await sent;
    assert.equal(h.chat.status, "ready");
    assert.deepEqual(h.chat.messages.at(-1)?.parts, [
        { type: "text", text: "Model content: 원문 그대로", state: "done", providerMetadata: undefined },
    ]);
    assert.deepEqual(h.toasts, []);
    assert.equal(h.storage.size, 0);
});

test("reconnection preserves task identity, ignores other tasks, and completes original output", async () => {
    const h = await createHarness();
    h.storage.set("langboard:editor-ai-run:card-1", "original-task");
    const resumed = h.chat.resumeStream();
    await h.flush();
    assert.equal(h.requests[0].eventName, "status");
    assert.equal(h.requests[0].data.task_id, "original-task");
    h.emit("statusResult", { task_id: "other-task", status: "failed" });
    h.emit("statusResult", { task_id: "original-task", status: "error", error_code: "unavailable" });
    h.emit("open");
    assert.equal(h.requests.at(-1)?.data.task_id, "original-task");
    h.emit("statusResult", { task_id: "original-task", status: "completed", output_text: "Recovered output" });
    await resumed;
    assert.equal(h.chat.status, "ready");
    assert.equal(
        h.requests.some((request) => request.eventName === "send"),
        false
    );
    assert.equal(h.storage.size, 0);
    assert.deepEqual(h.toasts, []);
    assert.equal(h.timers.size, 0);
});

test("local cancellation keeps run identity until server confirmation and emits no error toast", async () => {
    const h = await createHarness();
    const sent = h.chat.sendMessage({ text: "Hello" });
    await h.flush();
    h.abort();
    await sent;
    assert.equal(h.requests.find((request) => request.eventName === "abort")?.data.task_id, "task-1");
    assert.equal(h.storage.get("langboard:editor-ai-run:card-1"), "task-1");
    assert.equal(h.hasStream(), false);
    await h.chat.resumeStream();
    assert.equal(h.hasStream(), false);
    h.emit("aborted", { task_id: "task-1" });
    assert.equal(h.storage.size, 0);
    assert.equal(h.timers.size, 0);
    assert.equal(h.listeners.size, 0);
    assert.deepEqual(h.toasts, []);
});

test("editor graph progress is transient, allowlisted and does not alter generated text", async () => {
    const h = await createHarness();
    const sent = h.chat.sendMessage({ text: "Keep user text" });
    await h.flush();
    h.receive("progress", { task_id: "task-1", progress: { stage: "tool_started", output: "private result" } });
    await h.flush();
    assert.equal(h.progress.at(-1), "tool_started");
    h.receive("progress", { task_id: "task-1", progress: { stage: "private result" } });
    h.receive("progress", { task_id: "other-task", progress: { stage: "node_failed" } });
    h.receive("progress", { progress: { stage: "node_failed" } });
    h.receive("progress", { progress: null });
    h.receive("progress", {});
    await h.flush();
    assert.equal(h.progress.at(-1), "tool_started");
    h.receive("buffer", { message: "Visible answer" });
    h.receive("progress", { task_id: "task-1", progress: { stage: "node_completed" } });
    h.receive("end", { message: "Visible answer" });
    await sent;
    assert.equal(h.chat.status, "ready");
    assert.equal(h.chat.messages.length, 2);
    assert.deepEqual(JSON.parse(JSON.stringify(h.chat.messages[1].parts)), [{ type: "text", text: "Visible answer", state: "done" }]);
    assert.equal(h.hasStream(), false);
});
