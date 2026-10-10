import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import CardAppResources from "./CardAppResources";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
const params = new URLSearchParams(location.search);
const calls: { method?: string; url?: string; data: unknown }[] = [];
Object.assign(window, { resourceCalls: calls });
let revision: number | null = params.has("revoked") ? 3 : null;
let selected: string[] = params.has("revoked") ? ["00000000001"] : [];
let fail = params.has("lost");
api.defaults.adapter = async (config) => {
    const data = config.data ? JSON.parse(config.data) : null;
    calls.push({ method: config.method, url: config.url, data });
    if (config.method === "put") {
        selected = data.resource_uids;
        revision = (revision ?? 0) + 1;
        if (fail) {
            fail = false;
            throw new Error("Response lost after commit");
        }
    }
    const result = config.url?.endsWith("resource-selections")
        ? {
              items: params.has("revoked") ? [{ app_key: "example-app", connection_uid: "00000000002", revision, resource_uids: selected }] : [],
              next_cursor: null,
          }
        : config.url?.endsWith("resource-context")
          ? {
                items: params.has("revoked")
                    ? []
                    : [{ resource_uid: "00000000001", connection_uid: "00000000002", app_key: "example-app", name: "Example issue" }],
                next_cursor: null,
            }
          : { revision, resource_uids: selected };
    return { config, data: result, status: 200, statusText: "OK", headers: {} };
};
await i18n.changeLanguage("en-US");
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <div className="mx-auto max-w-xl p-4">
            <CardAppResources projectUID="00000000003" cardUID="00000000004" userUID="00000000005" />
        </div>
    </QueryClientProvider>
);
