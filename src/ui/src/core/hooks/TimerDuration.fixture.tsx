import { createRoot } from "react-dom/client";
import { ProjectCheckitem } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { TrackingRowTimeTaken } from "@/pages/DashboardPage/components/TrackingRow";
import BoardCardCheckitemTimer from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemTimer";
import "@/core/injection";
import i18n from "@/i18n";
declare global {
    interface Window {
        timerFixture: { accumulated(seconds: number): void; start(ago: number): void; pause(): void };
        pendingTimerCount(): number;
    }
}
const checkitem = ProjectCheckitem.Model.fromOne({
    uid: "timer-item",
    title: "Timer",
    card_uid: "card",
    checklist_uid: "list",
    status: "stopped",
    accumulated_seconds: 0,
    created_at: new Date(),
    updated_at: new Date(),
    is_checked: false,
});
Object.assign(window, {
    timerFixture: {
        accumulated: (seconds: number) => {
            checkitem.accumulated_seconds = seconds;
        },
        start: (ago: number) => {
            checkitem.timer_started_at = new Date(Date.now() - ago * 1000);
            checkitem.status = ProjectCheckitem.ECheckitemStatus.Started;
        },
        pause: () => {
            checkitem.status = ProjectCheckitem.ECheckitemStatus.Paused;
        },
    },
});
void i18n.changeLanguage("en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <ModelRegistry.ProjectCheckitem.Provider model={checkitem}>
            <output data-testid="tracking">
                <TrackingRowTimeTaken />
            </output>
            <section data-testid="detail">
                <BoardCardCheckitemTimer />
            </section>
        </ModelRegistry.ProjectCheckitem.Provider>
    )
);
