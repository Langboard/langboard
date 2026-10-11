import { createRoot } from "react-dom/client";
import { useState } from "react";
import { ProjectCard, ProjectCardAttachment } from "@/core/models";
import BoardOutlineSidebar from "@/pages/BoardPage/components/board/BoardOutlineSidebar";
import "@/i18n";
import "@/assets/styles/main.css";
const now = new Date();
for (const uid of ["first", "second"])
    ProjectCard.Model.fromOne({
        uid,
        created_at: now,
        updated_at: now,
        project_uid: "fixture",
        project_column_uid: "column",
        title: uid,
        description: { content: "" },
        order: 0,
    });
ProjectCardAttachment.Model.fromOne({
    uid: "attachment",
    card_uid: "second",
    created_at: now,
    updated_at: now,
    name: "proof.pdf",
    url: "",
    order: 0,
});
function Fixture() {
    const [cardUID, setCardUID] = useState("first");
    return (
        <main className="h-screen w-full max-w-sm">
            <button onClick={() => setCardUID(cardUID === "first" ? "second" : "first")}>Switch card</button>
            <BoardOutlineSidebar cardUID={cardUID} onRelations={() => {}} />
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
