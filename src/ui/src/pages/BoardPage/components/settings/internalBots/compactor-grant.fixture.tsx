import { useState } from "react";
import { createRoot } from "react-dom/client";
import { BoardCompactorGrantForm } from "./BoardCompactorGrant";
import "@/i18n";
import "@/assets/styles/main.css";

function Fixture() {
    const [profile, setProfile] = useState("compactor-one");
    const [visible, setVisible] = useState(true);
    return (
        <main className="mx-auto max-w-lg space-y-4 p-4">
            <button onClick={() => setProfile("compactor-two")}>Switch profile</button>
            <button onClick={() => setVisible(false)}>Leave settings</button>
            {visible && (
                <BoardCompactorGrantForm
                    key={profile}
                    projectUID="board-one"
                    profileUID={profile}
                    displayName={profile === "compactor-one" ? "Compactor one" : "Compactor two"}
                />
            )}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
