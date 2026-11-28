import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { AuthUser, ProjectCard } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useAuthStore from "@/core/stores/AuthStore";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import CardColumnHistory from "@/pages/BoardPage/components/card/CardColumnHistory";
import CardColumnEvent, { ColumnEvent } from "@/pages/BoardPage/components/card/CardColumnEvent";
import "@/i18n";
import "@/assets/styles/main.css";
const now = new Date();
const user = AuthUser.Model.fromOne({ uid: "history-user", created_at: now, updated_at: now });
useAuthStore.setState({ state: "loaded", pageLoaded: true, currentUser: user });
const cards = ["history-first", "history-second"].map((uid) =>
    ProjectCard.Model.fromOne({
        uid,
        project_uid: "fixture",
        project_column_uid: "column",
        count_comment: 0,
        current_auth_role_actions: [ProjectRole.EAction.Read],
        created_at: now,
        updated_at: now,
    })
);
function Fixture() {
    const [index, setIndex] = useState(0);
    const ref = useRef<HTMLDivElement>(null);
    const card = cards[index];
    return (
        <BoardCardProvider projectUID="fixture" card={card} currentUser={user} viewportRef={ref}>
            <button onClick={() => setIndex((value) => 1 - value)}>Switch history card</button>
            <CardColumnHistory card={card} />
            {new URLSearchParams(location.search).has("transitions") && (
                <section aria-label="Comment event examples" className="flex min-w-0 flex-col gap-4 p-4 text-xs">
                    {(
                        [
                            {
                                uid: "user-move",
                                activity_type: "card_moved",
                                from_column: { name: "Review" },
                                column: { name: "Doing" },
                                recorder: { type: "user", firstname: "History", lastname: "Worker" },
                            },
                            {
                                uid: "bot-move",
                                activity_type: "card_moved",
                                from_column: { name: "Doing" },
                                column: { name: "Review" },
                                recorder: { type: "bot", name: "검토 봇" },
                            },
                            { uid: "old-move", activity_type: "card_moved", column: { name: "Archived stage" } },
                            {
                                uid: "created",
                                activity_type: "card_created",
                                from_column: { name: "Not a source" },
                                column: { name: "Created stage" },
                            },
                            { uid: "invalid-time", activity_type: "card_moved", column: { name: "Invalid timestamp" }, created_at: "invalid" },
                        ] as (Omit<ColumnEvent, "created_at"> & { created_at?: string })[]
                    ).map((event) => (
                        <CardColumnEvent key={event.uid} event={{ created_at: "2026-10-01T12:00:00Z", ...event }} showTransition />
                    ))}
                </section>
            )}
        </BoardCardProvider>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
