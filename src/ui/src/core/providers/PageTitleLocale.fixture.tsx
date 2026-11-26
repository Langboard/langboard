import "@/i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { MemoryRouter } from "react-router";
import { useTranslation } from "react-i18next";
import { PageHeaderProvider, usePageHeader } from "./PageHeaderProvider";
import SignInPage from "@/pages/auth/SignInPage";
import EmailVerificationPage from "@/pages/AccountPage/EmailVerificationPage";
import PreferencesPage from "@/pages/AccountPage/PreferencesPage";
import ApiKeysPage from "@/pages/SettingsPage/ApiKeysPage";
import GlobalLabelsPage from "@/pages/SettingsPage/GlobalLabelsPage";
import ProjectPage from "@/pages/DashboardPage/ProjectPage";
const noop = () => {};
const user = { useField: () => [] } as unknown as Parameters<typeof GlobalLabelsPage>[0]["currentUser"];
function RawTitle({ name }: { name: string }) {
    const { setPageAliasRef } = usePageHeader();
    useEffect(() => {
        setPageAliasRef.current(name);
    }, [name, setPageAliasRef]);
    return null;
}
function Fixture() {
    const [screen, setScreen] = useState("signin");
    const [t, i18n] = useTranslation();
    return (
        <>
            <p>Synthetic authentication and data boundaries; production pages and title provider.</p>
            <output>{i18n.language}</output>
            {["en-US", "ko-KR", "ja-JP", "zh-CN"].map((locale) => (
                <button key={locale} onClick={() => i18n.changeLanguage(locale)}>
                    {locale}
                </button>
            ))}
            {["signin", "verify-email", "preferences", "keys", "labels", "dashboard", "Sign In", "Dashboard", "settings.API keys"].map((name) => (
                <button key={name} onClick={() => setScreen(name)}>
                    screen:{name}
                </button>
            ))}
            <h1>{t("auth.Sign in")}</h1>
            {screen === "signin" ? (
                <SignInPage />
            ) : screen === "verify-email" ? (
                <EmailVerificationPage />
            ) : screen === "preferences" ? (
                <PreferencesPage />
            ) : screen === "keys" ? (
                <ApiKeysPage />
            ) : screen === "labels" ? (
                <GlobalLabelsPage currentUser={user} />
            ) : screen === "dashboard" ? (
                <ProjectPage updateStarredProjects={noop} scrollAreaUpdater={[0, noop]} />
            ) : (
                <RawTitle name={screen} />
            )}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/?bEvt=synthetic-verification-token"]}>
            <PageHeaderProvider>
                <Fixture />
            </PageHeaderProvider>
        </MemoryRouter>
    </QueryClientProvider>
);
