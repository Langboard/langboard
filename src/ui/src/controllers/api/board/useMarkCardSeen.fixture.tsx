import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { api } from "@/core/helpers/Api";
import { ProjectCard } from "@/core/models";
import useMarkCardSeen from "./useMarkCardSeen";
import useCardReadState from "./useCardReadState";
import "@/i18n";

const projectUID = "seen-fixture";
const cardUID = "seen-card";
let boardReads = 0;
let readerReads = 0;
let receipt: unknown;
let serverUnread = false;
let releaseReceipt: () => void = () => {};
api.defaults.adapter = async (config) => {
    const response = { status: 200, statusText: "OK", headers: {}, config };
    if (config.method === "post") {
        await new Promise<void>((resolve) => {
            releaseReceipt = resolve;
        });
        return { ...response, data: receipt };
    }
    readerReads++;
    return { ...response, data: { readers: [] } };
};
const setCard = (sequence: number, unread: boolean, count = unread ? 3 : 0) =>
    ProjectCard.Model.fromOne({
        uid: cardUID,
        project_uid: projectUID,
        project_column_uid: "column",
        title: "Read card",
        description: "",
        order: 0,
        created_at: new Date(),
        updated_at: new Date(),
        last_change_seq: sequence,
        has_unread_change: unread,
        unread_change_count: count,
        unread_change_count_status: "exact",
    });
setCard(10, false);
function Fixture() {
    const [result, setResult] = useState("Ready");
    const board = useQuery({
        queryKey: [`get-cards-${projectUID}`],
        queryFn: async () => {
            const card = ProjectCard.Model.getModel(cardUID)!;
            setCard(card.last_change_seq!, serverUnread, serverUnread ? card.unread_change_count! : 0);
            return ++boardReads;
        },
        staleTime: Infinity,
    });
    const { readers } = useCardReadState(projectUID, cardUID);
    const seen = useMarkCardSeen();
    const start = (scenario: string) => {
        setCard(10, ["Unread", "Missing receipt", "Wrong card"].includes(scenario));
        serverUnread = ["Missing receipt", "Wrong card"].includes(scenario);
        receipt = scenario === "Missing receipt" ? undefined : { card_uid: scenario === "Wrong card" ? "other-card" : cardUID, seen_change_seq: 10 };
        setResult(`${scenario}: pending`);
        seen.mutate({ project_uid: projectUID, card_uid: cardUID }, { onSuccess: () => setResult(`${scenario}: complete`) });
    };
    return (
        <>
            <h1>Seen receipt fixture</h1>
            <p>{result}</p>
            <p>Unread: {String(ProjectCard.Model.getModel(cardUID)!.has_unread_change)}</p>
            <p>Count: {ProjectCard.Model.getModel(cardUID)!.unread_change_count}</p>
            <p>Board reads: {board.data ?? 0}</p>
            <p>Reader reads: {readers.data ? readerReads : 0}</p>
            {["Unchanged", "Unread", "New change", "Missing receipt", "Wrong card"].map((scenario) => (
                <button key={scenario} disabled={seen.isPending} onClick={() => start(scenario)}>
                    {scenario}
                </button>
            ))}
            <button onClick={() => releaseReceipt()}>Release receipt</button>
            <button
                onClick={() => {
                    setCard(11, true);
                    serverUnread = true;
                    releaseReceipt();
                }}
            >
                Publish intervening change
            </button>
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <Fixture />
    </QueryClientProvider>
);
