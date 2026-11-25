import i18n from "@/i18n";
import "@/assets/styles/main.css";
import "@/core/injection/StringExtensions";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { AuthProvider, useAuth } from "./AuthProvider";
import HeaderUserNotification from "@/components/Header/HeaderUserNotification";
import CardActivitySettings from "@/components/NotificationSetting/CardActivitySettings";
import Tooltip from "@/components/base/Tooltip";
import useAuthStore from "@/core/stores/AuthStore";
import NotificationCenterPage from "@/pages/NotificationCenterPage";
import { api } from "@/core/helpers/Api";
await i18n.changeLanguage(new URLSearchParams(location.search).get("lang") ?? "en-US");
useAuthStore.setState({ pageLoaded: true });
let notificationReads = 0;
const showActivity = new URLSearchParams(location.search).has("activity");
const showCenter = new URLSearchParams(location.search).has("center");
const showInbox = showCenter || new URLSearchParams(location.search).has("inbox");
const eventUIDs = ["event000001", "event000002"];
let observedRead: string[] = [];
let newActivity = false;
const queryClient = new QueryClient();
let activityReads = 0;
let resolveActivity: () => void;
const activityGate = new Promise<void>((resolve) => {
    resolveActivity = resolve;
});
const base = { created_at: new Date(), updated_at: new Date() };
const user = {
    ...base,
    uid: "fixture-owner",
    type: "user",
    email: "owner@example.invalid",
    firstname: "Owner",
    lastname: "Test",
    username: "owner",
    is_admin: false,
    user_groups: [],
    subemails: [],
    api_key_role_actions: [],
    setting_role_actions: [],
    mcp_role_actions: [],
};
api.defaults.adapter = async (config) => {
    let data: unknown = {};
    if (config.url?.endsWith("/auth/refresh")) data = { access_token: "synthetic-token" };
    if (config.url?.endsWith("/auth/me")) data = { user, bots: [] };
    if (config.url?.endsWith("/dashboard/projects")) data = { projects: [], columns: [] };
    if (config.url?.endsWith("/activity")) {
        if (++activityReads === 1) {
            await activityGate;
            data = { mode: "invalid" };
        } else {
            data = { mode: "comments_and_moves" };
        }
    }
    if (config.url?.endsWith("/notifications/inbox")) {
        notificationReads++;
        const threads =
            showInbox && !observedRead.length
                ? [
                      {
                          latest_event_uid: eventUIDs[1],
                          latest_event_type: "mentioned_in_comment",
                          latest_at: "2026-11-23T00:00:00Z",
                          project_uid: "fixture-board",
                          card_uid: "fixture-card",
                          card_title: "Grouped fixture card",
                          project_title: "Fixture board",
                          card_column: "Review",
                          card_priority: "P0",
                          subscription_reasons: ["creator", "mentioned"],
                          actor_name: "Fixture Actor",
                          unread_events: newActivity ? 3 : 2,
                          unread_mention: true,
                          observed_unread_event_uids: newActivity ? [...eventUIDs, "event000003"] : eventUIDs,
                          observed_state_event_uids: newActivity ? [...eventUIDs, "event000003"] : eventUIDs,
                      },
                  ]
                : [];
        const laterPage = config.params?.cursor === "fixture-next";
        if (showCenter && threads.length && laterPage) {
            threads.splice(0, 1, {
                ...threads[0],
                latest_event_uid: "event000005",
                latest_at: "2026-11-21T00:00:00Z",
                card_uid: "same-board-card",
                card_title: "Earlier card on first board",
                unread_events: 1,
                unread_mention: false,
                observed_unread_event_uids: ["event000005"],
                observed_state_event_uids: ["event000005"],
            });
        } else if (showCenter && threads.length) {
            threads.push({
                latest_event_uid: "event000004",
                latest_event_type: "card_moved",
                latest_at: "2026-11-22T00:00:00Z",
                project_uid: "second-board",
                card_uid: "second-card",
                card_title: "Other board card",
                project_title: "Second board",
                card_column: "Doing",
                card_priority: "P2",
                subscription_reasons: ["assignee"],
                actor_name: "Another actor",
                unread_events: 1,
                unread_mention: false,
                observed_unread_event_uids: ["event000004"],
                observed_state_event_uids: ["event000004"],
            });
        }
        data = { threads, next_cursor: showCenter && threads.length && !laterPage ? "fixture-next" : null, unread_count: threads.length };
        document.getElementById("reads")!.textContent = String(notificationReads);
    }
    if (config.url?.endsWith("/notifications/inbox/read-events")) {
        observedRead = JSON.parse(config.data).event_uids;
        document.getElementById("observed-read")!.textContent = observedRead.join(",");
        data = { updated_count: observedRead.length };
    }
    return { config, data, status: 200, statusText: "OK", headers: {} };
};
function Fixture() {
    const { currentUser } = useAuth();
    return (
        <>
            <output id="reads">0</output>
            {showInbox && <output id="observed-read" />}
            <output id="state">{currentUser ? "loaded" : "loading"}</output>
            {currentUser && !showCenter && <HeaderUserNotification currentUser={currentUser} />}
            {currentUser && showCenter && (
                <>
                    <button
                        onClick={() => {
                            newActivity = true;
                            void queryClient.invalidateQueries();
                        }}
                    >
                        Add fixture activity
                    </button>
                    <NotificationCenterPage />
                </>
            )}
            {currentUser && showActivity && (
                <div id="activity">
                    <CardActivitySettings projectUID="fixture-board" cardUID="fixture-card" />
                    <button onClick={resolveActivity}>Resolve activity read</button>
                </div>
            )}
        </>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={queryClient}>
        <MemoryRouter>
            <Tooltip.Provider>
                <AuthProvider>
                    <Fixture />
                </AuthProvider>
            </Tooltip.Provider>
        </MemoryRouter>
    </QueryClientProvider>
);
