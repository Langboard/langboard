import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import useChangeCardOrder from "@/controllers/api/board/useChangeCardOrder";
import CardFamilyMoveConfirmation from "@/pages/BoardPage/components/board/CardFamilyMoveConfirmation";
import "@/i18n";
import "@/assets/styles/main.css";

function Fixture() {
    const { mutateAsync, confirmation, isPending } = useChangeCardOrder({ interceptToast: true });
    const [result, setResult] = useState("idle");
    return (
        <>
            <button
                disabled={isPending}
                onClick={() => {
                    setResult("pending");
                    mutateAsync({ project_uid: "board", card_uid: "root", parent_uid: "target", order: 0 }).then(
                        () => setResult("moved"),
                        () => setResult("cancelled")
                    );
                }}
            >
                Move root
            </button>
            <output>{result}</output>
            <CardFamilyMoveConfirmation {...confirmation} />
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <Fixture />
    </QueryClientProvider>
);
