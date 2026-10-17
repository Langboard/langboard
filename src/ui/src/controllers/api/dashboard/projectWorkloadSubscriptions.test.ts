import assert from "node:assert/strict";
import test from "node:test";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { acquireProjectWorkload } from "./projectWorkloadSubscriptions.ts";

const settle = async () => {
    for (let turn = 0; turn < 15; turn++) await Promise.resolve();
};

for (const cached of [false, true]) {
    test(`activity during slow ${cached ? "cached" : "initial"} reads completes them and coalesces a fresh follow-up`, async (context) => {
        context.mock.timers.enable({ apis: ["setTimeout"] });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
        const key = ["get-dashboard-projects"];
        const requests: { signal: AbortSignal; resolve: (value: number) => void }[] = [];
        const observer = new QueryObserver(client, {
            queryKey: key,
            initialData: cached ? 0 : undefined,
            staleTime: Infinity,
            queryFn: ({ signal }) => new Promise<number>((resolve) => requests.push({ signal, resolve })),
        });
        const unsubscribe = observer.subscribe(() => {});
        let activity = () => {};
        const release = acquireProjectWorkload(client, ["a"], {
            subscribe: () => {},
            unsubscribe: () => {},
            listen: (_uid, callback) => {
                activity = callback;
                return () => {};
            },
            listenOpen: () => () => {},
        });
        context.after(() => {
            release();
            unsubscribe();
            client.clear();
        });
        const first = client.refetchQueries({ queryKey: key });
        activity();
        context.mock.timers.tick(250);
        await settle();
        assert.equal(requests[0].signal.aborted, false, "activity must not abort the user's slow read");
        assert.equal(requests.length, 1, "wait for the running read before fetching current activity");
        requests[0].resolve(1);
        await first;
        await settle();
        assert.equal(requests.length, 2, "read again after the event, not just reuse its older snapshot");
        for (let event = 0; event < 10; event++) {
            activity();
            context.mock.timers.tick(250);
            await settle();
        }
        assert.equal(requests[1].signal.aborted, false);
        assert.equal(requests.length, 2, "bursts cannot fan out or starve the pending read");
        requests[1].resolve(2);
        await settle();
        context.mock.timers.tick(250);
        await settle();
        assert.equal(requests.length, 3, "events during a read coalesce into one subsequent read");
        requests[2].resolve(3);
        await settle();
        assert.equal(client.getQueryData(key), 3);
        context.mock.timers.tick(500);
        await settle();
        assert.equal(requests.length, 3, "no polling after activity stops");
    });
}

test("releasing an owner during a slow read prevents its queued follow-up", async (context) => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    const key = ["get-dashboard-projects"];
    let resolve = (_value: number) => {};
    let reads = 0;
    const observer = new QueryObserver(client, {
        queryKey: key,
        queryFn: () => {
            reads++;
            return new Promise<number>((done) => {
                resolve = done;
            });
        },
    });
    const unsubscribe = observer.subscribe(() => {});
    let activity = () => {};
    const socket = {
        subscribe: () => {},
        unsubscribe: () => {},
        listen: (_uid: string, callback: () => void) => {
            activity = callback;
            return () => {};
        },
        listenOpen: () => () => {},
    };
    const release = acquireProjectWorkload(client, ["a"], socket);
    activity();
    context.mock.timers.tick(250);
    await settle();
    release();
    const replacement = acquireProjectWorkload(client, ["b"], socket);
    resolve(1);
    await settle();
    context.mock.timers.tick(500);
    await settle();
    assert.equal(reads, 1, "the released owner must not invalidate a replacement's query");
    replacement();
    unsubscribe();
    client.clear();
});

test("shared views retain topics, burst refresh, reconnect and last release", async (context) => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const listeners = new Map<string, () => void>();
    const subscribed: string[][] = [];
    const unsubscribed: string[][] = [];
    const order: string[] = [];
    const acknowledgements: (() => void)[] = [];
    let open = () => {};
    const client = {
        getQueryState: () => undefined,
        invalidateQueries: async () => {
            order.push("read");
        },
    };
    const socket = {
        subscribe: (uids: string[], acknowledged?: () => void) => {
            subscribed.push(uids);
            if (acknowledged) acknowledgements.push(acknowledged);
        },
        unsubscribe: (uids: string[]) => unsubscribed.push(uids),
        listen: (uid: string, callback: () => void) => {
            listeners.set(uid, callback);
            return () => {
                listeners.delete(uid);
            };
        },
        listenOpen: (callback: () => void) => {
            open = callback;
            return () => {
                open = () => {};
            };
        },
    };
    const first = acquireProjectWorkload(client as never, ["a", "b"], socket);
    const second = acquireProjectWorkload(client as never, ["a"], socket);
    assert.deepEqual(subscribed, [["a", "b"]]);
    acknowledgements.shift()!();
    listeners.get("a")!();
    listeners.get("b")!();
    listeners.get("a")!();
    context.mock.timers.tick(250);
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(order, ["read"]);
    first();
    assert.deepEqual(unsubscribed, [["b"]]);
    assert.equal(listeners.has("a"), true);
    open();
    assert.deepEqual(subscribed, [["a", "b"], ["a"]]);
    assert.deepEqual(order, ["read"], "reconnect must wait for topic acknowledgement");
    acknowledgements.shift()!();
    listeners.get("a")!();
    context.mock.timers.tick(250);
    await Promise.resolve();
    await Promise.resolve();
    assert.deepEqual(order, ["read", "read"]);
    listeners.get("a")!();
    second();
    context.mock.timers.tick(250);
    await Promise.resolve();
    assert.deepEqual(order, ["read", "read"]);
    assert.deepEqual(unsubscribed, [["b"], ["a"]]);
    assert.equal(listeners.size, 0);
});

test("late acknowledgement cannot refresh released or replacement owners", async (context) => {
    context.mock.timers.enable({ apis: ["setTimeout"] });
    const acknowledgements: (() => void)[] = [];
    let reconnect = () => {};
    let reads = 0;
    const client = {
        getQueryState: () => undefined,
        invalidateQueries: async () => {
            reads++;
        },
    };
    const socket = {
        subscribe: (_uids: string[], acknowledged?: () => void) => {
            if (acknowledged) acknowledgements.push(acknowledged);
        },
        unsubscribe: () => {},
        listen: () => () => {},
        listenOpen: (callback: () => void) => {
            reconnect = callback;
            return () => {};
        },
    };
    const release = acquireProjectWorkload(client as never, ["a"], socket);
    reconnect();
    const late = acknowledgements.splice(0);
    release();
    const replacement = acquireProjectWorkload(client as never, ["b"], socket);
    late.forEach((acknowledge) => acknowledge());
    context.mock.timers.tick(250);
    await Promise.resolve();
    assert.equal(reads, 0);
    acknowledgements.shift()!();
    context.mock.timers.tick(250);
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(reads, 1);
    replacement();
});
