import { createRoot } from "react-dom/client";
import { useState } from "react";
import BoardSettingsTeams from "./BoardSettingsTeams";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
void i18n.changeLanguage("en-US");
function Fixture() {
    const [active, setActive] = useState(false);
    const [board, setBoard] = useState("Board000001");
    const [account, setAccount] = useState("User0000001");
    return (
        <main className="w-full min-w-0 p-2">
            <button onClick={() => setActive(!active)}>Toggle teams</button>
            <button onClick={() => setBoard(board === "Board000001" ? "Board000002" : "Board000001")}>Switch board</button>
            <button onClick={() => setAccount(account === "User0000001" ? "User0000002" : "User0000001")}>Switch account</button>
            <div hidden={!active}>
                <BoardSettingsTeams projectUID={board} accountUID={account} active={active} />
            </div>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
