import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { ProjectCard, ProjectCheckitem } from "@/core/models";
import TrackingRow from "./TrackingRow";
import "@/core/injection";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

declare global {
    interface Window {
        trackingFixture: { restore(): void; remove(): void; rename(): void; refresh(): void };
    }
}
const restore = () =>
    ProjectCard.Model.fromOne(
        {
            uid: "tracking-card",
            project_uid: "tracking-project",
            project_column_uid: "tracking-column",
            title: "Authorized card",
            description: { content: "", format: "text" },
            order: 0,
            created_at: new Date(),
            updated_at: new Date(),
        },
        true
    );
const mode = new URLSearchParams(location.search).get("mode");
if (mode !== "missing") restore();
const checkitem = ProjectCheckitem.Model.fromOne({
    uid: "tracking-item",
    card_uid: "tracking-card",
    checklist_uid: "tracking-list",
    title: "Tracked task",
    status: ProjectCheckitem.ECheckitemStatus.Stopped,
    order: 0,
    accumulated_seconds: 120,
    is_checked: mode === "completed",
    created_at: new Date(),
    updated_at: new Date(),
});
window.trackingFixture = {
    restore,
    remove: () => ProjectCard.Model.deleteModel("tracking-card"),
    rename: () => {
        ProjectCard.Model.getModel("tracking-card")!.title = "Updated card";
    },
    refresh: () => {
        checkitem.title = "Updated task";
    },
};
void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <BrowserRouter>
            <main>{mode !== "empty" && <TrackingRow checkitem={checkitem} className="relative" data-testid="tracking-row" />}</main>
        </BrowserRouter>
    )
);
