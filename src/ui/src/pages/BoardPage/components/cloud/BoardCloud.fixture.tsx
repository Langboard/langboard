import { createRoot } from "react-dom/client";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BoardCloud from "./BoardCloud";
import BoardCloudPage from "../../BoardCloudPage";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
api.defaults.baseURL = location.origin;
const base = { created_at: new Date(), updated_at: new Date() };
useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "cloud-reader" }), state: "loaded" });
const project = Project.Model.fromOne({ ...base, uid: "cloud-board", title: "Cloud fixture" });
function Fixture() {
    const location = useLocation();
    const [panel, setPanel] = useState(true);
    const [uid, setUID] = useState("cloud-board");
    return (
        <div style={{ height: new URLSearchParams(window.location.search).get("compact") === "true" ? 360 : 820, width: "100%", maxWidth: 900 }}>
            <button onClick={() => setPanel((value) => !value)}>Toggle panel</button>
            <button onClick={() => setUID("other-board")}>Switch board</button>
            <button onClick={() => useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...base, uid: "other-reader" }) })}>
                Switch actor
            </button>
            <output role="none">{location.pathname}</output>
            {panel ? <BoardCloud key={uid} projectUID={uid} panel /> : <BoardCloudPage project={project} />}
        </div>
    );
}
void i18n.changeLanguage(new URLSearchParams(location.search).get("lang") ?? "en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter initialEntries={["/board/cloud-board"]}>
                <Fixture />
            </MemoryRouter>
        </QueryClientProvider>
    )
);
