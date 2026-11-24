import type { ErrorEvent } from "@sentry/browser";

const transportCodes = new Set(["ECONNABORTED", "ERR_NETWORK", "ETIMEDOUT", "ERR_BAD_RESPONSE"]);

let capture: ((error: unknown) => void) | undefined;

/** Optional initialized transport; never loads an SDK for an unconfigured host. */
export function reportBrowserError(error: unknown) {
    capture?.(error);
}

/** Preserve diagnostics without copying application scope or user-provided text. */
export function diagnosticEvent(event: ErrorEvent): ErrorEvent | null {
    if (!event.exception?.values?.length) return null;
    return {
        type: undefined,
        event_id: event.event_id,
        timestamp: event.timestamp,
        platform: "javascript",
        level: "error",
        release: event.release,
        environment: event.environment,
        tags: diagnosticTags(event.tags),
        exception: {
            values: event.exception.values.map((exception) => ({
                type: /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(exception.type ?? "") ? exception.type : "Error",
                value: "Application error (content omitted)",
                stacktrace: {
                    frames: exception.stacktrace?.frames?.map((frame) => ({
                        filename: assetPath(frame.filename),
                        lineno: frame.lineno,
                        colno: frame.colno,
                        in_app: frame.in_app,
                    })),
                },
            })),
        },
    };
}

function diagnosticTags(tags?: ErrorEvent["tags"]): Record<string, string> {
    const result: Record<string, string> = {};
    if (typeof tags?.transport_code === "string" && transportCodes.has(tags.transport_code)) {
        result.transport_code = tags.transport_code;
    }
    if (typeof tags?.http_status === "string" && /^[45][0-9]{2}$/.test(tags.http_status)) {
        result.http_status = tags.http_status;
    }
    return result;
}

function transportTags(error: unknown): Record<string, string> {
    if (!error || typeof error !== "object") return {};
    const failure = error as { code?: unknown; response?: { status?: unknown } };
    return diagnosticTags({
        transport_code: typeof failure.code === "string" ? failure.code : undefined,
        http_status: typeof failure.response?.status === "number" ? String(failure.response.status) : undefined,
    });
}

function assetPath(filename?: string) {
    if (!filename) return undefined;
    try {
        const url = new URL(filename);
        // Keep shipped source coordinates, not board/card URLs or query tokens.
        return /^\/assets\/[A-Za-z0-9_.-]+\.js$/.test(url.pathname) ? url.pathname : undefined;
    } catch {
        return undefined;
    }
}

export async function initializeBrowserTelemetry(config: { dsn: string; environment: string; release: string }) {
    if (!config.dsn) return;
    const url = new URL(config.dsn);
    if (
        url.protocol !== "https:" ||
        !/^[A-Za-z0-9]+$/.test(url.username) ||
        url.password ||
        !/^\/(?:[A-Za-z0-9_-]+\/)*\d+$/.test(url.pathname) ||
        url.search ||
        url.hash
    ) {
        throw new Error("Invalid public telemetry DSN");
    }
    const Sentry = await import("@sentry/browser");
    Sentry.init({
        ...config,
        dataCollection: {
            userInfo: false,
            cookies: false,
            httpHeaders: false,
            httpBodies: [],
            urlQueryParams: false,
            graphQL: { document: false, variables: false },
            genAI: { inputs: false, outputs: false },
            databaseQueryData: false,
            queues: false,
            stackFrameVariables: false,
            frameContextLines: 0,
        },
        defaultIntegrations: false,
        integrations: [Sentry.globalHandlersIntegration(), Sentry.browserApiErrorsIntegration(), Sentry.dedupeIntegration()],
        beforeSend: diagnosticEvent,
    });
    capture = (error) => {
        Sentry.captureException(error, { tags: transportTags(error) });
    };
}
