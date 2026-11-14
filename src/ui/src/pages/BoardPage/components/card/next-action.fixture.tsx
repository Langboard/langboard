import "@/assets/styles/main.css";
import "@/i18n";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import useChangeCardDetails from "@/controllers/api/card/useChangeCardDetails";
import CardNextAction, { NextActionField } from "./CardNextAction";
import { ProjectCard } from "@/core/models";
function Fixture() {
    const params = new URLSearchParams(location.search);
    const [value, setValue] = useState<string | null>("Confirm deployment");
    const [card] = useState(
        () =>
            new ProjectCard.Model({
                uid: "fixture-board-card",
                project_uid: "fixture-board",
                next_action: params.has("empty") ? null : params.has("long") ? "Long authored action ".repeat(50) : "Confirm deployment",
            })
    );
    const [canEdit, setCanEdit] = useState(!params.has("readonly"));
    const [, i18n] = useTranslation();
    const { mutateAsync } = useChangeCardDetails({ interceptToast: true });
    return (
        <main className="mx-auto max-w-md p-4">
            <button onClick={() => void i18n.changeLanguage("ko-KR")}>한국어</button>
            <button onClick={() => setCanEdit((current) => !current)}>Toggle permission</button>
            {params.has("board") && (
                <aside data-board-next-action>
                    <CardNextAction card={card} compact />
                </aside>
            )}
            <NextActionField
                value={value}
                canEdit={canEdit}
                save={async (next) => {
                    const result = (await mutateAsync({ project_uid: "fixture-board", card_uid: "fixture-card", next_action: next })) as {
                        next_action: string | null;
                    };
                    setValue(result.next_action);
                    card.next_action = result.next_action;
                }}
            />
            <output>{value ?? "empty"}</output>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <Fixture />
    </QueryClientProvider>
);
