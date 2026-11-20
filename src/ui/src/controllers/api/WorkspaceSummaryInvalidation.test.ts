import assert from "node:assert/strict";
import test from "node:test";
import { InfiniteQueryObserver, QueryClient } from "@tanstack/react-query";
import { acquireWorkspaceSummaryTopics, resetWorkspaceSummaryQueries } from "./WorkspaceSummaryInvalidation.ts";
import type { ISocketContext } from "@/core/providers/SocketProvider";
import type { ESocketTopic } from "@langboard/core/enums";

test("native invalidation cancels stale pages synchronously and reauthorizes only active matching queries", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const key = ["card-relation-summaries", "actor:1:1", "board", "card"];
    const calls: { signal: AbortSignal; cursor: unknown; resolve: (value: { title: string; next: null | string }) => void }[] = [];
    const observer = new InfiniteQueryObserver(client, {
        queryKey: key,
        initialPageParam: null,
        getNextPageParam: (page: { title: string; next: null | string }) => page.next,
        queryFn: ({ signal, pageParam }) =>
            new Promise<{ title: string; next: null | string }>((resolve) => calls.push({ signal, cursor: pageParam, resolve })),
    });
    const off = observer.subscribe(() => {});
    calls[0].resolve({ title: "Revoked title", next: "next" });
    await observer.refetch({ cancelRefetch: false });
    const nextPage = observer.fetchNextPage();
    const unrelated = ["card-relation-summaries", "actor:1:1", "other-board", "card"];
    client.setQueryData(unrelated, { pages: [{ title: "Other board" }], pageParams: [null] });
    const inactive = ["card-relation-summaries", "old-actor:1:1", "board", "card"];
    client.setQueryData(inactive, { pages: [{ title: "Old private title" }], pageParams: [null] });
    const event = { card_uid: "card", relationships_invalidated: true };
    resetWorkspaceSummaryQueries(client, "card-relation-summaries", "board", event, "card");
    resetWorkspaceSummaryQueries(client, "card-relation-summaries", "board", event, "card");
    assert.equal(client.getQueryData(key), undefined);
    assert.equal(client.getQueryData(inactive), undefined);
    assert.ok(client.getQueryData(unrelated));
    assert.equal(calls[1].signal.aborted, true);
    assert.deepEqual(
        calls.map((call) => call.cursor),
        [null, "next", null]
    );
    calls[1].resolve({ title: "Late revoked metadata", next: null });
    calls[2].resolve({ title: "Current authorized title", next: null });
    await nextPage;
    assert.equal(observer.getCurrentResult().data?.pages[0].title, "Current authorized title");
    off();
    client.clear();
});

test("Wiki summary and native page share topic ownership and preserve unrelated borrowed subscriptions", () => {
    const operations: string[] = [];
    const opens = new Map<string, () => void>();
    const socket = {
        subscribe: (_topic: string, ids: string[]) => operations.push(`subscribe:${ids}`),
        unsubscribe: (_topic: string, ids: string[]) => operations.push(`unsubscribe:${ids}`),
        isSubscribed: (_topic: string, uid: string) => uid === "borrowed",
        on: (props: { eventKey: string; callback: () => void }) => opens.set(props.eventKey, props.callback),
        off: (props: { eventKey: string }) => opens.delete(props.eventKey),
    } as unknown as ISocketContext;
    const topic = "board_wiki" as ESocketTopic.BoardWiki;
    const summary = acquireWorkspaceSummaryTopics(socket, [[topic, "board"]]);
    const nativePage = acquireWorkspaceSummaryTopics(socket, [[topic, "board"]]);
    const borrowed = acquireWorkspaceSummaryTopics(socket, [[topic, "borrowed"]]);
    summary();
    assert.deepEqual(operations, ["subscribe:board"]);
    for (const open of opens.values()) open();
    assert.deepEqual(operations, ["subscribe:board", "subscribe:board", "subscribe:borrowed"]);
    nativePage();
    borrowed();
    assert.equal(opens.size, 0);
    assert.deepEqual(operations, ["subscribe:board", "subscribe:board", "subscribe:borrowed", "unsubscribe:board"]);
});

test("one native open event refreshes each project and summary kind once across duplicate consumers", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const keys = [
        ["card-relation-summaries", "actor:1:1", "home", "root"],
        ["card-relation-summaries", "actor:1:1", "home", "child"],
        ["card-relation-summaries", "actor:1:1", "remote", "root"],
        ["wiki-summaries", "actor:1:1", "home"],
    ];
    const readers = keys.map((queryKey) => {
        const requests: { resolve: (value: { title: string; next: null }) => void }[] = [];
        const observer = new InfiniteQueryObserver(client, {
            queryKey,
            initialPageParam: null,
            getNextPageParam: (page: { title: string; next: null }) => page.next,
            queryFn: () => new Promise<{ title: string; next: null }>((resolve) => requests.push({ resolve })),
        });
        const off = observer.subscribe(() => {});
        return { observer, requests, off };
    });
    readers.forEach(({ requests }) => requests[0].resolve({ title: "Protected before reconnect", next: null }));
    await Promise.all(readers.map(({ observer }) => observer.refetch({ cancelRefetch: false })));
    const open = new Event("open");
    for (let consumer = 0; consumer < 3; consumer++) {
        resetWorkspaceSummaryQueries(client, "card-relation-summaries", "home", open);
        resetWorkspaceSummaryQueries(client, "card-relation-summaries", "remote", open);
        resetWorkspaceSummaryQueries(client, "wiki-summaries", "home", open);
    }
    for (let index = 0; index < keys.length; index++) {
        assert.equal(client.getQueryData(keys[index]), undefined, `protected cache synchronously cleared: ${keys[index]}`);
        assert.equal(readers[index].requests.length, 2, "each query performs exactly one current-authority read");
        readers[index].requests[1].resolve({ title: "Current authorized metadata", next: null });
    }
    await Promise.all(readers.map(({ observer }) => observer.refetch({ cancelRefetch: false })));
    readers.forEach(({ observer, off }) => {
        assert.equal(observer.getCurrentResult().data?.pages[0].title, "Current authorized metadata");
        off();
    });
    client.clear();
});
