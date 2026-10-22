import "@/core/injection";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";
import { getUserSettingsStore } from "@/core/stores/UserSettingsStore";
import { focusCard, useOpenCards } from "./OpenCardsStore";
import { recentOpenCards } from "./OpenCardsData";
import RecentCardsSection from "./RecentCardsSection";
import ExplorerSection from "./ExplorerSection";
import CardFlipTray from "@/pages/BoardPage/components/card/CardFlipTray";
import { useCardFlipStore } from "@/pages/BoardPage/components/card/CardFlipStore";
import "@/i18n";
import "@/assets/styles/main.css";
const count = Number(new URLSearchParams(location.search).get("count") || 14);
const multiBoard = new URLSearchParams(location.search).has("multi-board");
getUserSettingsStore().updateSettingsByKey("open_cards", {
    fixture: Array.from({ length: count }, (_, index) => ({
        projectUID: multiBoard ? `fixture-${index}` : "fixture",
        cardUID: String(index),
        title: `Card ${index}`,
        pinned: index === 0,
        lastFocusedAt: `2026-10-02T00:00:${String(index).padStart(2, "0")}Z`,
    })),
});
if (new URLSearchParams(location.search).has("tray")) useCardFlipStore.getState().flip("fixture", "fixture", { uid: "tray", title: "Tray card" });
function Fixture() {
    const [userUID, setUserUID] = useState("fixture");
    const cards = recentOpenCards(useOpenCards(userUID));
    const [expanded, setExpanded] = useState(false);
    const [projectsExpanded, setProjectsExpanded] = useState(false);
    const [projectsCollapsed, setProjectsCollapsed] = useState(false);
    const [collapsed, setCollapsed] = useState(false);
    const [active, setActive] = useState(!new URLSearchParams(window.location.search).has("hidden"));
    const location = useLocation();
    return (
        <div className="h-screen w-full max-w-lg p-2">
            {new URLSearchParams(window.location.search).has("tray") && <CardFlipTray userUID={userUID} projectUID="fixture" />}
            <button onClick={() => setUserUID((value) => (value === "fixture" ? "other" : "fixture"))}>Switch account</button>
            <button onClick={() => focusCard(userUID, { projectUID: "fixture", cardUID: "new", title: "New card" })}>Open new card</button>
            <p data-testid="route">{location.pathname}</p>
            <button onClick={() => setActive((value) => !value)}>Toggle explorer visibility</button>
            <button onClick={() => focusCard("fixture", { projectUID: "fixture", cardUID: "0", title: "Card 0" })}>Read oldest again</button>
            <nav className="flex h-[65vh] min-h-0 flex-col overflow-hidden" aria-label="Explorer">
                <div className="shrink-0 p-2">Explorer</div>
                <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" data-explorer-section-list="">
                    <section className="mb-3 shrink-0">
                        Current project
                        <br />
                        Board
                        <br />
                        Wiki
                    </section>
                    <RecentCardsSection
                        active={active}
                        userUID={userUID}
                        cards={cards}
                        collapsed={collapsed}
                        expanded={expanded}
                        onExpand={() => setExpanded((shown) => !shown)}
                        onToggle={() => setCollapsed((shown) => !shown)}
                    />
                    <ExplorerSection
                        title="Favorites"
                        count={20}
                        expanded={projectsExpanded}
                        collapsed={projectsCollapsed}
                        onToggle={() => setProjectsCollapsed((value) => !value)}
                        onExpand={() => setProjectsExpanded((value) => !value)}
                    >
                        {Array.from({ length: projectsExpanded ? 20 : 10 }, (_, index) => (
                            <p key={index}>Project {index}</p>
                        ))}
                    </ExplorerSection>
                </div>
            </nav>
        </div>
    );
}
const router = createMemoryRouter([{ path: "*", element: <Fixture /> }], { initialEntries: ["/board/fixture"] });
createRoot(document.getElementById("root")!).render(<RouterProvider router={router} />);
