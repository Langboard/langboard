export interface DevtronConnection {
    connection_uid: string;
    instance_url: string;
    ownership: string;
    state: string;
    revision: string;
}
export interface DevtronAccounts {
    app_revision: string;
    available: boolean;
    ownership_scopes: (string | null)[];
    binding: {
        uid: string;
        revision: string;
        state: string;
        granted_capabilities: string[];
    } | null;
    items: DevtronConnection[];
    next_cursor: string | null;
}
export interface DevtronResource {
    resource_uid: string;
    resource_type: string;
    external_resource_id: string;
    is_selected: boolean;
    revision: number;
    access_state: string;
    health: string;
}
export interface DevtronResources {
    app_revision: string;
    binding_uid: string;
    binding_revision: string;
    identity_revision: string;
    resource_types: string[];
    items: DevtronResource[];
    next_cursor: string | null;
}
export interface DevtronScope {
    kind: "inventory" | "native" | "installed" | "external";
    first: string;
    second: string;
}
export interface DevtronProviderError {
    ok: false;
    error: { code: string; status?: number };
}
const uid = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9]{11}$/.test(value);
const revision = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const cursor = (value: unknown) => value === null || uid(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");

export function readDevtronAccounts(value: DevtronAccounts): DevtronAccounts {
    if (
        !value ||
        !revision(value.app_revision) ||
        typeof value.available !== "boolean" ||
        !cursor(value.next_cursor) ||
        !Array.isArray(value.ownership_scopes) ||
        !value.ownership_scopes.every((item) => item === null || uid(item)) ||
        !Array.isArray(value.items) ||
        value.items.length > 50 ||
        (value.binding !== null &&
            (!value.binding || !uid(value.binding.uid) || !revision(value.binding.revision) || !strings(value.binding.granted_capabilities)))
    )
        throw new Error("Invalid connection metadata");
    value.items.forEach(readDevtronConnection);
    return value;
}

export function readDevtronConnection(row: DevtronConnection): DevtronConnection {
    if (
        !row ||
        !uid(row.connection_uid) ||
        !revision(row.revision) ||
        typeof row.instance_url !== "string" ||
        !["personal", "organization"].includes(row.ownership) ||
        !["connected", "disconnected", "pending", "revoked"].includes(row.state)
    )
        throw new Error("Invalid connection metadata");
    return row;
}

export function readDevtronResources(value: DevtronResources): DevtronResources {
    if (
        !value ||
        !revision(value.app_revision) ||
        !uid(value.binding_uid) ||
        !revision(value.binding_revision) ||
        !revision(value.identity_revision) ||
        !strings(value.resource_types) ||
        !cursor(value.next_cursor) ||
        !Array.isArray(value.items) ||
        value.items.length > 50 ||
        value.items.some(
            (row) =>
                !uid(row.resource_uid) ||
                typeof row.resource_type !== "string" ||
                typeof row.external_resource_id !== "string" ||
                row.external_resource_id.length > 512 ||
                typeof row.is_selected !== "boolean" ||
                !Number.isSafeInteger(row.revision) ||
                row.revision < 0 ||
                !["unknown", "granted", "denied", "revoked"].includes(row.access_state) ||
                !["unknown", "healthy", "degraded", "unavailable"].includes(row.health)
        )
    )
        throw new Error("Invalid resource metadata");
    return value;
}

export function secureDevtronInput(input: { input_uid: string; input_url: string }, origin: string): string {
    if (!/^[A-Za-z0-9_-]{43}$/.test(input.input_uid) || typeof input.input_url !== "string" || input.input_url.length > 2048)
        throw new Error("Invalid credential input");
    const url = new URL(input.input_url);
    if (url.origin !== origin || url.username || url.password || url.search || url.hash || url.pathname !== `/secret-input/${input.input_uid}`)
        throw new Error("Invalid credential input");
    return url.href;
}

export async function devtronTarget(scope: DevtronScope): Promise<{
    deployment_scope?: Record<string, string | number>;
    external_id: string | null;
    helm_app_id?: string;
}> {
    const integer = (value: string) => {
        if (typeof value !== "string" || !/^[1-9][0-9]{0,9}$/.test(value) || Number(value) > 1_000_000_000)
            throw new Error("Invalid deployment identity");
        return Number(value);
    };
    if (scope.kind === "inventory") return { external_id: null };
    if (scope.kind === "native") {
        const appId = integer(scope.first),
            environmentId = integer(scope.second);
        return {
            deployment_scope: { appId, environmentId },
            external_id: `app-${appId}-env-${environmentId}`,
        };
    }
    if (scope.kind === "installed") {
        const installedAppId = integer(scope.first);
        return {
            deployment_scope: { installedAppId },
            external_id: `helm-app-${installedAppId}`,
        };
    }
    if (scope.kind !== "external") throw new Error("Invalid deployment scope");
    const [cluster, namespace, release, extra] = scope.first.split("|");
    const label = /^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$/;
    integer(cluster ?? "");
    if (
        extra !== undefined ||
        !namespace ||
        namespace.length > 63 ||
        !label.test(namespace) ||
        !release ||
        release.length > 53 ||
        !release.split(".").every((part) => label.test(part))
    )
        throw new Error("Invalid Helm identity");
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(scope.first));
    const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    return {
        deployment_scope: { helmAppId: scope.first },
        external_id: `helm-release-${hash}`,
        helm_app_id: scope.first,
    };
}
