import { createRoot } from "react-dom/client";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { ProjectCheckitem } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import BoardCardCheckitemTimer from "./BoardCardCheckitemTimer";

const checkitem = new ProjectCheckitem.Model({
    uid: "timer-regression",
    card_uid: "card",
    checklist_uid: "checklist",
    title: "Timer regression",
    status: ProjectCheckitem.ECheckitemStatus.Started,
    accumulated_seconds: 10,
    timer_started_at: new Date(),
});

createRoot(document.getElementById("root")!).render(
    <>
        <ModelRegistry.ProjectCheckitem.Provider model={checkitem}>
            <BoardCardCheckitemTimer />
        </ModelRegistry.ProjectCheckitem.Provider>
        <button
            onClick={() => {
                checkitem.accumulated_seconds = 27;
                checkitem.timer_started_at = undefined;
                checkitem.status = ProjectCheckitem.ECheckitemStatus.Paused;
            }}
        >
            Receive paused snapshot
        </button>
        <button
            onClick={() => {
                checkitem.timer_started_at = new Date(Date.now() - 5000);
            }}
        >
            Receive corrected start
        </button>
    </>
);
