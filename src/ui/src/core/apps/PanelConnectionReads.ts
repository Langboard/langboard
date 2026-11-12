import type { ConnectionReadRequest, JsonValue } from "@langboard/app-protocol";

interface Permission {
    app_revision: string;
    binding_revision: string;
    connection_reads?: string[];
}

/** Frame parameters cannot replace host-pinned authority or select an HTTP endpoint. */
export async function readPanelConnection(
    permission: Permission,
    params: JsonValue,
    signal: AbortSignal,
    isCurrent: () => boolean,
    post: (body: ConnectionReadRequest & { app_revision: string; binding_revision: string }, signal: AbortSignal) => Promise<JsonValue>
): Promise<JsonValue> {
    const current = () => {
        if (signal.aborted || !isCurrent()) throw new Error("Panel connection scope changed");
    };
    current();
    if (
        !params || typeof params !== "object" || Array.isArray(params) ||
        Object.keys(params).some((key) => !["connection_uid", "read_key", "input"].includes(key)) ||
        typeof params.connection_uid !== "string" || !/^[A-Za-z0-9]{11}$/.test(params.connection_uid) ||
        typeof params.read_key !== "string" || !permission.connection_reads?.includes(params.read_key) ||
        !params.input || typeof params.input !== "object" || Array.isArray(params.input)
    ) throw new Error("Unsupported panel connection read");
    const result = await post({
        connection_uid: params.connection_uid,
        read_key: params.read_key,
        input: params.input,
        app_revision: permission.app_revision,
        binding_revision: permission.binding_revision,
    }, signal);
    current();
    return result;
}
