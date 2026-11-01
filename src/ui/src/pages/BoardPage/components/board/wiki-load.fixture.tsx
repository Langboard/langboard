import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import { AuthUser } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import BoardWikiSidebar from "./BoardWikiSidebar";
import "@/i18n";
import "@/assets/styles/main.css";

useAuthStore.setState({
    currentUser: AuthUser.Model.fromOne({ uid: "wiki-recovery-reader", created_at: new Date(), updated_at: new Date() }),
    state: "loaded",
});
const client = new QueryClient();
let reads = 0;
let fail = true;
let release: (() => void) | undefined;
api.defaults.adapter = async (config) => {
    document.querySelector("output")!.textContent = String(++reads);
    if (reads > 1)
        await new Promise<void>((resolve) => {
            release = resolve;
        });
    if (fail)
        throw new AxiosError("Synthetic unavailable wikis", "ERR_BAD_RESPONSE", config, undefined, {
            status: 503,
            statusText: "Unavailable",
            data: {},
            headers: {},
            config,
        });
    return {
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        data: {
            items: [{ wiki_uid: "fixture-wiki", title: "Cached wiki", is_public: true }],
            next_cursor: null,
            limit: 25,
        },
    };
};
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={client}>
            <output>0</output>
            <button
                onClick={() => {
                    fail = false;
                    release?.();
                }}
            >
                Recover server
            </button>
            <button
                onClick={() => {
                    fail = true;
                    void client.invalidateQueries({ queryKey: ["wiki-summaries"] });
                }}
            >
                Fail refresh
            </button>
            <button onClick={() => release?.()}>Release request</button>
            <BoardWikiSidebar projectUID="fixture-project" />
        </QueryClientProvider>
    </MemoryRouter>
);
