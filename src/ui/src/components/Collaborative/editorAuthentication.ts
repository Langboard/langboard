import type { HocuspocusProvider } from "@hocuspocus/provider";

// A refused credential/permission is not a transient transport failure. Keep
// the Y.Doc for the existing retry action, but stop the automatic socket loop.
export const createEditorAuthentication = (getProvider: () => HocuspocusProvider | undefined, getAuthorizedUrl: () => string | null) => ({
    token: () => {
        const url = getAuthorizedUrl();
        return url ? (new URL(url).searchParams.get("authorization") ?? "") : "";
    },
    onAuthenticationFailed: () => getProvider()?.configuration.websocketProvider.disconnect(),
    // Hocuspocus emits disconnect before deciding whether to schedule a retry;
    // its provider-level close callback runs after that decision.
    onDisconnect: ({ event }: { event: { code: number } }) => {
        if (event.code === 4401 || event.code === 4403) {
            getProvider()?.configuration.websocketProvider.disconnect();
        }
    },
});
