import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import { AuthUser, ProjectCard } from "@/core/models";
import { BoardCardProvider } from "@/core/providers/BoardCardProvider";
import CardColumnHistory from "@/pages/BoardPage/components/card/CardColumnHistory";
import "@/i18n";
import "@/assets/styles/main.css";
const now = new Date();
const user = AuthUser.Model.fromOne({ uid: "history-user", created_at: now, updated_at: now });
const cards = ["history-first", "history-second"].map((uid) =>
    ProjectCard.Model.fromOne({
        uid,
        project_column_uid: "column",
        count_comment: 0,
        current_auth_role_actions: [],
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
        </BoardCardProvider>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
