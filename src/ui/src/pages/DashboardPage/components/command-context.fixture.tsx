import { lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider, useLocation, useNavigate } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AttachmentSearchTarget from "@/pages/BoardPage/components/card/attachment/AttachmentSearchTarget";
import { API_URL } from "@/constants";
import useAuthStore from "@/core/stores/AuthStore";
import ProjectQuickSwitcher from "./ProjectQuickSwitcher";
import { PROJECT_QUICK_SWITCHER_EVENT } from "./ProjectDiscovery";
import { WORKBENCH_OPEN_CHANGES_EVENT, WORKBENCH_OPEN_MY_WORK_EVENT, WORKBENCH_OPEN_RELATIONS_EVENT } from "./WorkbenchCommands";
import { api } from "@/core/helpers/Api";
import "@/i18n";
import "@/assets/styles/main.css";

// Two visually distinct pages allow native viewer verification beyond the iframe URL.
function twoPagePdf() {
    const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 500] /Contents 6 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
        ...["0.9 0.1 0.1", "0.1 0.7 0.2"].map((color, index) => {
            const stream = `${color} rg 0 0 400 500 re f 0 0 0 rg BT /F1 30 Tf 40 250 Td (NATIVE PAGE ${index + 1}) Tj ET`;
            return `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
        }),
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ];
    let pdf = "%PDF-1.4\n";
    const offsets = [0];
    objects.forEach((object, index) => {
        offsets.push(pdf.length);
        pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
    });
    const start = pdf.length;
    pdf += `xref\n0 8\n0000000000 65535 f \n${offsets
        .slice(1)
        .map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`)
        .join("")}trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`;
    return new Blob([pdf], { type: "application/pdf" });
}

// Delay the panel's inner content indefinitely; the command focus owner must still exist.
const PendingContent = lazy(() => new Promise<{ default: () => null }>(() => {}));
api.defaults.adapter = async (config) => {
    if (config.url?.includes("/file/")) {
        const mode = new URLSearchParams(window.location.search).get("pdf");
        document.documentElement.dataset.pdfRequestState = "pending";
        try {
            if (mode === "denied") throw new Error("Unauthorized fixture attachment");
            if (mode === "slow") await new Promise((resolve) => setTimeout(resolve, 800));
            return {
                status: 200,
                statusText: "OK",
                headers: {},
                config,
                data: mode === "wrong-mime" ? new Blob(["invalid"], { type: "text/html" }) : twoPagePdf(),
            };
        } finally {
            document.documentElement.dataset.pdfRequestState = "settled";
        }
    }
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
                                  page: new URLSearchParams(window.location.search).has("pdf") ? 2 : 3,
                                  pages: new URLSearchParams(window.location.search).has("pdf") ? [2, 1] : [3, 5],
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
    const navigate = useNavigate();
    const pdfMode = new URLSearchParams(window.location.search).has("pdf");
    const pdfFile = pdfMode
        ? {
              name: "Report.pdf",
              url:
                  new URLSearchParams(window.location.search).get("pdf") === "unsafe-url"
                      ? "https://untrusted.invalid/report.pdf"
                      : `${API_URL}/file/authorized/card_attachment/report.pdf`,
          }
        : undefined;
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
            {pdfMode && (
                <>
                    <button onClick={() => navigate("/board/fixture/document?attachment=paged-attachment&source-pages=2%2C1")}>
                        Open PDF page two
                    </button>
                    <input aria-label="Source route" onChange={(event) => navigate(event.target.value)} />
                </>
            )}
            <button onClick={() => useAuthStore.getState().removeToken()}>Change account session</button>
            {location.pathname.split("/").length === 4 && (
                <section aria-label="Authorized card attachments">
                    <AttachmentSearchTarget attachmentUID="attachment">
                        <p>Meeting.pdf attachment receiver</p>
                    </AttachmentSearchTarget>
                    <AttachmentSearchTarget attachmentUID="paged-attachment" file={pdfFile}>
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
