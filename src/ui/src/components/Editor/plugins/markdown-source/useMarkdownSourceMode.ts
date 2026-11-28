import { useSyncExternalStore } from "react";

const MODE_EVENT = "langboard-markdown-source-mode";
const DESKTOP_QUERY = "(min-width: 1024px) and (hover: hover) and (pointer: fine)";
const memory = new Map<string, boolean>();
export const sourceModeKey = (userUID: string) => `langboard:markdown-source:${encodeURIComponent(userUID)}:v1`;
const subscribe = (callback: () => void) => {
    const storage = (event: StorageEvent) => {
        if (event.key) memory.delete(event.key);
        else memory.clear();
        callback();
    };
    window.addEventListener("storage", storage);
    window.addEventListener(MODE_EVENT, callback);
    return () => {
        window.removeEventListener("storage", storage);
        window.removeEventListener(MODE_EVENT, callback);
    };
};
export function useMarkdownSourceMode(userUID: string, eligible: boolean) {
    const key = sourceModeKey(userUID);
    const enabled = useSyncExternalStore(
        subscribe,
        () => {
            if (memory.has(key)) return memory.get(key)!;
            try {
                const saved = localStorage.getItem(key);
                if (saved !== null) return saved !== "off";
            } catch {
                /* Restricted storage still supports this session's preference. */
            }
            return memory.get(key) ?? true;
        },
        () => true
    );
    const previewFirst = useSyncExternalStore(
        subscribe,
        () => {
            if (memory.has(`${key}:layout`)) return memory.get(`${key}:layout`)!;
            try {
                return localStorage.getItem(`${key}:layout`) === "preview-first";
            } catch {
                return memory.get(`${key}:layout`) ?? false;
            }
        },
        () => false
    );
    const desktop = useSyncExternalStore(
        (callback) => {
            const media = window.matchMedia(DESKTOP_QUERY);
            media.addEventListener("change", callback);
            return () => media.removeEventListener("change", callback);
        },
        () => window.matchMedia(DESKTOP_QUERY).matches,
        () => false
    );
    const toggle = () => {
        memory.set(key, !enabled);
        try {
            localStorage.setItem(key, enabled ? "off" : "on");
        } catch {
            /* Keep the in-memory preference. */
        }
        window.dispatchEvent(new Event(MODE_EVENT));
    };
    const swap = () => {
        memory.set(`${key}:layout`, !previewFirst);
        try {
            localStorage.setItem(`${key}:layout`, previewFirst ? "source-first" : "preview-first");
        } catch {
            /* Keep the session preference. */
        }
        window.dispatchEvent(new Event(MODE_EVENT));
    };
    return { available: eligible && !!userUID && desktop, enabled: eligible && !!userUID && desktop && enabled, previewFirst, toggle, swap };
}
