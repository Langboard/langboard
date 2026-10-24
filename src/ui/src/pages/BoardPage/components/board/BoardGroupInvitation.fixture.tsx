import { createRoot } from "react-dom/client";
import BoardGroupInvitation from "./BoardGroupInvitation";
import { api } from "@/core/helpers/Api";
import "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
createRoot(document.getElementById("root")!).render(
    <main className="w-full min-w-0 p-2">
        <BoardGroupInvitation projectUID="board-local" />
    </main>
);
