// Routes are restoration hints only. BoardProxy still loads each board with current authorization.
type SplitStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const key = (userUID: string) => `langboard:board-split:v1:${encodeURIComponent(userUID)}`;

function boardUID(entry: unknown): string | undefined {
    if (typeof entry !== "string" || entry.length > 2048 || /[\\\x00-\x20\x7f]/.test(entry)) return;
    return /^\/board\/([\w-]+)(?:\/[\w-]+)*\/?(?:[?#].*)?$/.exec(entry)?.[1];
}

export function restoreBoardSplit(storage: SplitStorage, userUID: string | undefined, entry: string, desktop: boolean): string[] {
    const fallback = [entry];
    if (!userUID || !desktop || !boardUID(entry)) return fallback;
    try {
        const raw = storage.getItem(key(userUID));
        if (!raw || raw.length > 8192) return fallback;
        const saved = JSON.parse(raw);
        if (saved.version !== 1 || !Array.isArray(saved.entries) || saved.entries.length !== 2) return fallback;
        const uids = saved.entries.map(boardUID);
        if (uids.some((uid: string | undefined) => !uid) || new Set(uids).size !== 2) return fallback;
        const owner = uids.indexOf(boardUID(entry));
        if (owner < 0) return fallback;
        // The actual address wins over an older remembered card/wiki route. Never redirect on restoration.
        return saved.entries.map((savedEntry: string, index: number) => index === owner ? entry : savedEntry);
    } catch {
        return fallback;
    }
}

export function persistBoardSplit(storage: SplitStorage, userUID: string | undefined, entries: string[], desktop: boolean): void {
    if (!userUID || !desktop) return;
    try {
        const uids = entries.map(boardUID);
        if (entries.length === 2 && uids.every(Boolean) && new Set(uids).size === 2) {
            storage.setItem(key(userUID), JSON.stringify({ version: 1, entries }));
        } else {
            storage.removeItem(key(userUID));
        }
    } catch {
        // Storage can be blocked or full; the in-memory workspace must remain usable.
    }
}
