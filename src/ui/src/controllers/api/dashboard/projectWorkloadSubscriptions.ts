import type { QueryClient } from "@tanstack/react-query";

interface SocketLease {
    subscribe: (uids: string[], onSubscribed?: () => void) => void;
    unsubscribe: (uids: string[]) => void;
    listen: (uid: string, callback: () => void) => () => void;
    listenOpen: (callback: () => void) => () => void;
}

// One Dashboard topic owner per QueryClient, shared by Explorer, palette and home.
const clients = new WeakMap<
    QueryClient,
    {
        references: number;
        projects: Map<string, { references: number; off: () => void }>;
        timer?: ReturnType<typeof setTimeout>;
        refreshing?: boolean;
        dirty?: boolean;
        offOpen: () => void;
        offBoardRead: () => void;
    }
>();

export function acquireProjectWorkload(client: QueryClient, uids: string[], socket: SocketLease): () => void {
    let state = clients.get(client);
    const firstOwner = !state;
    if (!state) {
        const projects = new Map<string, { references: number; off: () => void }>();
        state = {
            references: 0,
            projects,
            offBoardRead: () => {},
            offOpen: socket.listenOpen(() => {
                // Close the reconnect gap only after the server confirms topic ownership.
                if (projects.size) socket.subscribe([...projects.keys()], () => refresh());
            }),
        };
        clients.set(client, state);
    }
    const owned = state;
    owned.references++;
    const refresh = () => {
        if (clients.get(client) !== owned || !owned.references) return;
        owned.dirty = true;
        if (owned.timer || owned.refreshing) return;
        // Bounded bursts share one authorized batch read; no per-card fetch or poll.
        owned.timer = setTimeout(async () => {
            owned.timer = undefined;
            owned.refreshing = true;
            owned.dirty = false;
            const queryKey = ["get-dashboard-projects"];
            try {
                // Let a slow snapshot finish, then read changes that arrived after it started.
                // Cancel/restart on every event can starve an active dashboard indefinitely.
                await client.refetchQueries({ queryKey, type: "active", fetchStatus: "fetching" }, { cancelRefetch: false });
                if (clients.get(client) === owned && owned.references) {
                    await client.invalidateQueries({ queryKey }, { cancelRefetch: false });
                }
            } finally {
                owned.refreshing = false;
                if (owned.dirty) refresh();
            }
        }, 250);
    };
    if (firstOwner) {
        // Native board snapshots advance board_seen_seq without a dashboard event.
        // Fence preceding snapshots once for all mounted dashboard consumers.
        owned.offBoardRead = client.getQueryCache().subscribe((event) => {
            if (
                event.type !== "updated" ||
                event.action.type !== "success" ||
                typeof event.query.queryKey[0] !== "string" ||
                !event.query.queryKey[0].startsWith("get-cards-") ||
                typeof event.query.queryKey[2] !== "string"
            )
                return;
            void client.cancelQueries({ queryKey: ["get-dashboard-projects", event.query.queryKey[2]] }).then(refresh);
        });
    }
    const newUIDs: string[] = [];
    for (const uid of new Set(uids)) {
        const current = owned.projects.get(uid);
        if (current) current.references++;
        else {
            owned.projects.set(uid, { references: 1, off: socket.listen(uid, refresh) });
            newUIDs.push(uid);
        }
    }
    if (newUIDs.length) socket.subscribe(newUIDs, refresh);
    let released = false;
    return () => {
        if (released) return;
        released = true;
        const removed: string[] = [];
        for (const uid of new Set(uids)) {
            const current = owned.projects.get(uid);
            if (!current || --current.references > 0) continue;
            current.off();
            owned.projects.delete(uid);
            removed.push(uid);
        }
        if (removed.length) socket.unsubscribe(removed);
        if (!--owned.references) {
            if (owned.timer) clearTimeout(owned.timer);
            owned.offOpen();
            owned.offBoardRead();
            clients.delete(client);
        }
    };
}
