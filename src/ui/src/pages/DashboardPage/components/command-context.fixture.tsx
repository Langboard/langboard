import { lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AttachmentSearchTarget from "@/pages/BoardPage/components/card/attachment/AttachmentSearchTarget";
import useAuthStore from "@/core/stores/AuthStore";
import ProjectQuickSwitcher from "./ProjectQuickSwitcher";
import { PROJECT_QUICK_SWITCHER_EVENT } from "./ProjectDiscovery";
import { WORKBENCH_OPEN_CHANGES_EVENT, WORKBENCH_OPEN_MY_WORK_EVENT, WORKBENCH_OPEN_RELATIONS_EVENT } from "./WorkbenchCommands";
import { api } from "@/core/helpers/Api";
import "@/i18n";
import "@/assets/styles/main.css";

// Delay the panel's inner content indefinitely; the command focus owner must still exist.
const PendingContent = lazy(() => new Promise<{ default: () => null }>(() => {}));
api.defaults.adapter = async (config) => {
    const search = config.url?.includes("/cards/context");
    const session = useAuthStore.getState().getSessionVersion();
    if (search && config.params.input_value === "slow") await new Promise((resolve) => setTimeout(resolve, 1000));
    return {
        status: 200,
        statusText: "OK",
        headers: {},
        config,
        data: search
            ? {
                  cards: [
                      {
                          uid: `${config.params.input_value}${session ? "-new-session" : ""}`,
                          title: `Found ${config.params.input_value}${session ? " new session" : ""}`,
                          project_column_name: "Backlog",
                          document_matches: [
                              { attachment_uid: "attachment", filename: "Meeting.pdf", snippet: "전사검색 日本語 中文 document" },
                              {
                                  attachment_uid: "paged-attachment",
                                  filename: "Report.pdf",
                                  snippet: "Page-specific excerpt",
                                  page: 3,
                                  pages: [3, 5],
                                  chunk_id: "chunk-one",
                                  generation: "generation-two",
                                  url: "https://untrusted.invalid/should-never-open",
                                  provenance_scope: "chunk",
                              },
                          ],
                      },
                  ],
              }
            : config.url?.includes("/search")
              ? { items: [], next_cursor: null }
              : { projects: [], columns: [] },
    };
};
function Panel({ mode }: { mode: string }) {
    const content = (
        <div data-workbench-command-context={mode} tabIndex={-1} role="region" aria-label={mode} className="h-40 w-full">
            <Suspense fallback={<p>Panel content pending</p>}>
                <PendingContent />
            </Suspense>
        </div>
    );
    return window.innerWidth < 768 ? (
        <aside aria-label={mode} tabIndex={-1}>
            <button onClick={() => {}}>Close panel</button>
            {content}
        </aside>
    ) : (
        content
    );
}
function Fixture() {
    const location = useLocation();
    const [mode, setMode] = useState("");
    useEffect(() => {
        const pairs = [
            [WORKBENCH_OPEN_MY_WORK_EVENT, "my-work"],
            [WORKBENCH_OPEN_CHANGES_EVENT, "changes"],
            [WORKBENCH_OPEN_RELATIONS_EVENT, "relations"],
        ];
        const handlers = pairs.map(([event, next]) => ({ event, handler: () => setMode(next) }));
        handlers.forEach(({ event, handler }) => window.addEventListener(event, handler));
        return () => handlers.forEach(({ event, handler }) => window.removeEventListener(event, handler));
    }, []);
    return (
        <>
            <button data-command-palette-trigger onClick={() => window.dispatchEvent(new Event(PROJECT_QUICK_SWITCHER_EVENT))}>
                Open palette
            </button>
            <p data-testid="route">
                {location.pathname}
                {location.search}
                {location.hash}
            </p>
            <button onClick={() => useAuthStore.getState().removeToken()}>Change account session</button>
            {location.pathname.split("/").length === 4 && (
                <section aria-label="Authorized card attachments">
                    <AttachmentSearchTarget attachmentUID="attachment">
                        <p>Meeting.pdf attachment receiver</p>
                    </AttachmentSearchTarget>
                    <AttachmentSearchTarget attachmentUID="paged-attachment">
                        <p>Report.pdf attachment receiver</p>
                    </AttachmentSearchTarget>
                </section>
            )}
            {mode && (
                <>
                    <div style={{ display: "none" }} data-workbench-command-context={mode} tabIndex={-1}>
                        Hidden panel copy
                    </div>
                    <Panel mode={mode} />
                </>
            )}
            <ProjectQuickSwitcher />
        </>
    );
}
const router = createMemoryRouter([{ path: "*", element: <Fixture /> }], { initialEntries: ["/board/fixture"] });
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
    </QueryClientProvider>
);
