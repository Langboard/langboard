import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BoardSettingsMcpConsent from "../BoardPage/components/settings/BoardSettingsMcpConsent";
import "@/i18n";
import "@/assets/styles/main.css";
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <BoardSettingsMcpConsent connectionUID="connection-one" userUID="owner" onBusy={() => {}} />
    </QueryClientProvider>
);
