import { createRoot } from "react-dom/client";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BoardMcpConnection from "./BoardMcpConnection";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
void i18n.changeLanguage("en-US");
const queryClient = new QueryClient();
function Fixture() {
    const [board, setBoard] = useState("BoardA");
    return (
        <QueryClientProvider client={queryClient}>
            <main className="w-full min-w-0">
                <button onClick={() => setBoard(board === "BoardA" ? "BoardB" : "BoardA")}>Switch board</button>
                <BoardMcpConnection projectUID={board} projectTitle={board} userUID="User000001" />
            </main>
        </QueryClientProvider>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
