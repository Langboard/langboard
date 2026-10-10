import { createRoot } from "react-dom/client";
import { useState } from "react";
import AppPanel from "./AppPanel";
import { invalidatePanelSessions, panelStateCache, activePanelDisposals } from "@/core/apps/PanelSession";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
const app: CatalogApp = {
    key: "example-app",
    name: "Independent resources",
    version: "1.0.0",
    app_revision: "a".repeat(64),
    panel: { url: "https://panel.example.invalid/panel", name: "Independent resources" },
    capabilities: ["panels.render", "resources.read"],
    workflow_requirements: null,
    resources: { selected_count: 1, access_counts: {}, health_counts: {}, connection_counts: {} },
    binding: {
        uid: "binding",
        revision: "b".repeat(64),
        state: "enabled",
        granted_capabilities: ["panels.render", "resources.read"],
        stage_transitions_enabled: false,
    },
};
function Fixture() {
    const [open, setOpen] = useState(true);
    return (
        <div style={{ height: 600 }}>
            <button onClick={invalidatePanelSessions}>Revoke panel sessions</button>
            <button
                onClick={() => {
                    const key = JSON.stringify(["actor", "board", app.key, app.version]);
                    document.querySelector("output")!.textContent = JSON.stringify({
                        state: panelStateCache.get(key),
                        active: activePanelDisposals.size,
                    });
                }}
            >
                Inspect sessions
            </button>
            <output />
            {open ? <AppPanel app={app} projectUID="board" userUID="actor" onClose={() => setOpen(false)} /> : <p>Panel closed</p>}
        </div>
    );
}
void i18n.changeLanguage("en-US").then(() => createRoot(document.getElementById("root")!).render(<Fixture />));
