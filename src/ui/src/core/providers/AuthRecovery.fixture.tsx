import "@/i18n";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { BrowserRouter } from "react-router";
import { AuthProvider, useAuth } from "./AuthProvider";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useAuthStore from "@/core/stores/AuthStore";
import { api } from "@/core/helpers/Api";
import { ROUTES } from "@/core/routing/constants";

const initialPath = new URLSearchParams(location.search).get("initial") ?? ROUTES.DASHBOARD.ROUTE;
history.replaceState(null, "", initialPath);
api.defaults.baseURL = location.origin;

function State() {
    const { state, currentUser } = useAuthStore();
    const route = usePageLocation();
    return <output aria-label="Auth state">{JSON.stringify({ state, user: currentUser?.uid ?? null, pathname: route.pathname })}</output>;
}

function Fixture() {
    const { signIn, signOut } = useAuth();
    const route = usePageLocation();
    const navigate = usePageNavigateRef();
    const [result, setResult] = useState("idle");
    const protectedRead = () => {
        setResult("pending");
        void api.get("/__auth-recovery-protected").then(
            () => setResult("fulfilled"),
            () => setResult("rejected")
        );
    };
    return (
        <main>
            <h1>{route.pathname === ROUTES.SIGN_IN.EMAIL ? "Sign in" : "Protected page"}</h1>
            <button onClick={protectedRead}>Protected request</button>
            <button onClick={() => void signOut().catch(() => setResult("logout rejected"))}>Logout</button>
            <button onClick={() => void signIn("synthetic-session-b")}>Replace session</button>
            <button onClick={() => navigate(ROUTES.DASHBOARD.ROUTE)}>Open protected page</button>
            <output aria-label="Request result">{result}</output>
        </main>
    );
}

createRoot(document.getElementById("root")!).render(
    <BrowserRouter>
        <State />
        <AuthProvider>
            <Fixture />
        </AuthProvider>
    </BrowserRouter>
);
