import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import type { DevtronResource } from "@/core/apps/DevtronSettings";
import { DevtronSettings } from "./BoardSettingsDevtron";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
// Controlled component transport only; deployed authorization/provider acceptance is separate.
const options = new URLSearchParams(location.search);
const writes: { method: string; url: string; body: Record<string, unknown> }[] = [];
const reads: string[] = [];
Object.assign(window, { devtronWrites: writes, devtronReads: reads });
const revision = "a".repeat(64);
let bindingRevision = "b".repeat(64);
let connection = {
    connection_uid: "connection1",
    instance_url: "https://devtron.example.invalid",
    ownership: "personal",
    state: "connected",
    revision: "c".repeat(64),
};
let registered = options.has("connected");
let rows: DevtronResource[] = [];
let unknown = options.has("unknown");
let preflightCount = 0;
api.defaults.adapter = async (config) => {
    const url = config.url ?? "",
        method = config.method ?? "get";
    const body = JSON.parse(config.data ?? "{}") as Record<string, unknown>;
    if (method === "get") reads.push(url);
    else writes.push({ method, url, body });
    const respond = (data: unknown) => ({
        config,
        status: 200,
        statusText: "OK",
        headers: {},
        data,
    });
    const fail = () => {
        throw new AxiosError("Authority denied", "ERR_BAD_REQUEST", config, undefined, {
            config,
            status: 403,
            statusText: "Forbidden",
            headers: {},
            data: {},
        });
    };
    if (url.endsWith("/disconnect")) {
        connection = {
            ...connection,
            state: "disconnected",
            revision: "d".repeat(64),
        };
        rows = rows.map((row) => ({
            ...row,
            access_state: "revoked",
            revision: row.revision + 1,
        }));
        return respond(connection);
    }
    if (url.endsWith("/connections")) {
        if (method === "post") {
            registered = true;
            return respond(connection);
        }
        preflightCount++;
        if (options.has("deny") && preflightCount > 1) return fail();
        return respond({
            app_revision: options.has("stale") && preflightCount > 1 ? "f".repeat(64) : revision,
            available: true,
            ownership_scopes: [null],
            binding: {
                uid: "binding0001",
                revision: bindingRevision,
                state: "enabled",
                granted_capabilities: ["resources.read"],
            },
            items: registered ? [connection] : [],
            next_cursor: null,
        });
    }
    if (url.endsWith("/verify")) {
        rows = rows.map((row) => ({
            ...row,
            access_state: "granted",
            health: "unknown",
            revision: row.revision + 1,
        }));
        return respond({
            ok: true,
            resource: {
                resource_uid: rows[0].resource_uid,
                access_revision: rows[0].revision,
                access_state: "granted",
                health: "unknown",
            },
        });
    }
    if (url.endsWith("/resources")) {
        if (method === "put") {
            rows = [
                {
                    resource_uid: "resource001",
                    resource_type: "application",
                    external_resource_id: String(body.external_resource_id),
                    is_selected: !!body.selected,
                    revision: (rows[0]?.revision ?? 0) + 1,
                    access_state: body.selected ? "unknown" : "revoked",
                    health: "unknown",
                },
            ];
            bindingRevision = "e".repeat(64);
            if (unknown) {
                unknown = false;
                throw new Error("Result unknown after persistence");
            }
            return respond(rows[0]);
        }
        return respond({
            app_revision: revision,
            binding_uid: "binding0001",
            binding_revision: bindingRevision,
            identity_revision: "d".repeat(64),
            resource_types: ["application"],
            items: rows,
            next_cursor: null,
        });
    }
    if (url.endsWith("/discover"))
        return respond({
            resources: [
                {
                    appId: 17,
                    appName: "Example app",
                    environmentId: 4,
                    environmentName: "Canary",
                },
            ],
        });
    throw new Error("Unexpected fixture request");
};
await i18n.changeLanguage(options.get("lang") ?? "en-US");
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <main className="mx-auto max-w-2xl p-4">
            <DevtronSettings
                projectUID="project0001"
                userUID="actor000001"
                canEditBasicInfo={!options.has("readonly")}
                app={{ key: "devtron-panel", app_revision: revision } as CatalogApp}
            />
        </main>
    </QueryClientProvider>
);
