import { createRoot } from "react-dom/client";
import { useState } from "react";
import { AuthUser } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import MailSettingsPage from "./MailSettingsPage";
import settings from "./Route";
import { matchRoutes } from "react-router";
import { ROUTES } from "@/core/routing/constants";
import "@/i18n";
import "@/assets/styles/main.css";
function Fixture() {
    const [actor, setActor] = useState(new URLSearchParams(location.search).has("member") ? "member" : "admin");
    const user = AuthUser.Model.fromOne({ uid: actor, is_admin: actor !== "member" } as AuthUser.Interface);
    return (
        <>
            <button onClick={() => setActor(actor === "admin" ? "other" : "admin")}>Change actor</button>
            <button onClick={() => setActor("member")}>Revoke admin</button>
            <button onClick={() => useAuthStore.getState().removeToken()}>Change session</button>
            <output data-testid="native-mail-route" hidden>
                {matchRoutes(settings.routes, ROUTES.SETTINGS.MAIL)?.at(-1)?.route.path}
            </output>
            <MailSettingsPage currentUser={user} />
        </>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
