import { createRoot } from "react-dom/client";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router";
import CreateProjectFormDialog from "./CreateProjectFormDialog";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
await i18n.changeLanguage(new URLSearchParams(location.search).get("lang") ?? "en-US");
function Fixture() {
    const [opened, setOpened] = useState(true);
    const [created, setCreated] = useState<string[]>([]);
    const location = useLocation();
    const embedded = new URLSearchParams(window.location.search).has("embedded");
    return (
        <>
            <output>{JSON.stringify({ opened, created, path: location.pathname })}</output>
            <CreateProjectFormDialog
                opened={opened}
                setOpened={setOpened}
                onCreated={embedded ? (uid) => setCreated((items) => [...items, uid]) : undefined}
            />
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
