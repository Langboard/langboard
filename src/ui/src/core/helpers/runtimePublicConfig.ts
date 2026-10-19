const endpointKeys = ["API_URL", "PUBLIC_UI_URL", "SOCKET_URL"] as const;
type EndpointKey = (typeof endpointKeys)[number];
type PublicEndpoints = Record<EndpointKey, string>;

declare global {
    var __LANGBOARD_RUNTIME_CONFIG__: unknown;
}

/** Resolve public endpoints before any API/socket clients are initialized. */
export function runtimePublicConfig(defaults: PublicEndpoints, runtime: unknown, production: boolean): PublicEndpoints {
    if (!production || runtime === undefined) return defaults;
    if (!runtime || typeof runtime !== "object" || Array.isArray(runtime)) {
        throw new Error("Public runtime configuration failed to load");
    }
    const supplied = runtime as Record<string, unknown>;
    if (Object.keys(supplied).some((key) => !endpointKeys.includes(key as EndpointKey))) {
        throw new Error("Invalid public runtime configuration");
    }
    const resolved = { ...defaults };
    for (const key of endpointKeys) {
        if (!(key in supplied)) continue;
        const value = supplied[key];
        const fail = () => new Error(`Invalid public runtime endpoint: ${key}`);
        if (
            typeof value !== "string" ||
            !/^(https?|wss?):\/\/[^/]/.test(value) ||
            /[\s\\"'<>?#]/.test(value) ||
            [...value].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
        ) {
            throw fail();
        }
        let url: URL;
        try {
            url = new URL(value);
        } catch {
            throw fail();
        }
        const protocols = key === "SOCKET_URL" ? ["http:", "https:", "ws:", "wss:"] : ["http:", "https:"];
        if (!protocols.includes(url.protocol) || !url.hostname || url.username || url.password || value.split("/")[2].includes("@")) {
            throw fail();
        }
        resolved[key] = value;
    }
    return resolved;
}
