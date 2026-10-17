import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";

export function hasPanelAccess(app: CatalogApp) {
    return (
        app.is_available === true &&
        !!app.panel &&
        app.capabilities.includes("panels.render") &&
        app.binding?.state === "enabled" &&
        app.binding.granted_capabilities.includes("panels.render")
    );
}
