import { createRoot } from "react-dom/client";
import { useState } from "react";
import type { TCardPriority } from "@langboard/core/constants";
import CardPriorityField from "./CardPriorityField";
import CardPriorityPopover from "./CardPriorityPopover";
import { getCardVisualSignals } from "../board/CardVisualSignals";
import useCardMotionVisibility from "@/core/hooks/useCardMotionVisibility";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

function Fixture() {
    const [priority, setPriority] = useState<TCardPriority>("P2");
    const [disabled, setDisabled] = useState(false);
    const [completed, setCompleted] = useState(false);
    const [open, setOpen] = useState(false);
    const visibility = useCardMotionVisibility();
    const signals = getCardVisualSignals({
        priority,
        completed,
        now: new Date(),
        checklist: { completed: 0, total: 0 },
        agentRunning: true,
        ...visibility,
        visible: visibility.visible && !open,
    });
    const save = async (next: TCardPriority) => {
        const response = await fetch("/__priority", { method: "PUT", body: JSON.stringify({ priority: next }) });
        if (!response.ok) throw new Error("Priority rejected");
        setPriority((await response.json()).priority);
    };
    return (
        <main style={{ padding: 24, width: 440 }}>
            <div
                ref={visibility.ref}
                data-testid="card"
                data-card-motion={signals.motion}
                data-priority={priority}
                className={signals.motion === "critical" || signals.motion === "emergency" ? "board-card-priority-pulse relative" : "relative"}
            >
                {new URLSearchParams(location.search).has("popover") ? (
                    <CardPriorityPopover
                        value={priority}
                        disabled={disabled}
                        save={save}
                        onOpenChange={setOpen}
                        effectsEnabled={!signals.suppressed}
                    />
                ) : (
                    <CardPriorityField value={priority} disabled={disabled} save={save} />
                )}
            </div>
            <button onClick={() => setDisabled((value) => !value)}>Toggle permission</button>
            <button onClick={() => setPriority("P3")}>Socket update</button>
            <button onClick={() => setCompleted(true)}>Complete</button>
            <output data-testid="confirmed">{priority}</output>
            <div style={{ height: 1800 }} />
        </main>
    );
}
void i18n.changeLanguage("en-US").then(() => createRoot(document.getElementById("root")!).render(<Fixture />));
