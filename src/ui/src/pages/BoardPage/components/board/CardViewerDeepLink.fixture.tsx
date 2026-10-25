import { PageNavigationScope } from "@/core/providers/PageNavigationScope";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import { StrictMode, lazy, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { SocketProvider } from "@/core/providers/SocketProvider";
import { PageHeaderProvider } from "@/core/providers/PageHeaderProvider";
import { BoardController } from "@/core/providers/BoardController";
import BoardProxy from "@/pages/BoardPage";
import DashboardProxy from "@/pages/DashboardPage";
import BoardCardPage from "@/pages/BoardPage/BoardCardPage";
import useGetCards from "@/controllers/api/board/useGetCards";
import Button from "@/components/base/Button";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser } from "@/core/models";
import ResizableSidebar from "@/components/ResizableSidebar";
import Floating from "@/components/base/Floating";
import ActivityRail from "@/components/Layout/ActivityRail";
import Header from "@/components/Header";
import { cn } from "@/core/utils/ComponentUtils";
import CardFlipTray from "@/pages/BoardPage/components/card/CardFlipTray";
import { SkeletonBoard } from "@/pages/BoardPage/components/board/Board";

/**
 * Deep-link entry harness for the card viewer.
 *
 * Reproduces the cold /board/:projectUID/:cardUID mount sequence with the real
 * providers, the real BoardCardPage, and the same conditional + keying
 * structure as BoardProxy/BoardProxyDisplay (src/pages/BoardPage/index.tsx):
 *
 *   {currentUser && project ? (... <BoardCardPage key={cardUID} embedded/>) : <Skeleton/>}
 *
 * Every timing phase is configurable through query parameters so a spec can
 * race auth, project, and card data exactly like a real cold load:
 *   ?projectDelay=ms&cardDelay=ms&strictMode=0|1&lateSuspense=0|1&lateSuspenseAt=ms
 *
 * Instrumentation (installed before the first render):
 *   - window.__events: viewer mount/unmount + card-viewer-open animation starts
 *   - window.__viewerMounted: number of [data-card-viewer] elements ever mounted
 *   - window.__openAnimationStarts: times card-viewer-open started
 */

const query = new URLSearchParams(window.location.search);
const delay = (name: string) => Number(query.get(name) ?? 0);
const PROJECT_DELAY = delay("projectDelay");
// Non-production builds of the app mount under React.StrictMode (main.tsx),
// which double-invokes mounts; mirror that for remount-driven replays.
const STRICT_MODE = query.get("strictMode") === "1";
// Mirror the app-root Suspense (Router.tsx): any late lazy chunk suspending
// after the card viewer mounted unmounts the whole children subtree and
// remounts it on resolve, replaying mount-driven animations.
const LATE_SUSPENSE = query.get("lateSuspense") === "1";
const LATE_SUSPENSE_AT = delay("lateSuspenseAt") || 800;

const LateChunk = lazy(
    () =>
        import("./CardViewerDeepLinkLateChunk.fixture").then(
            (module) => new Promise<typeof module>((resolve) => window.setTimeout(() => resolve(module), 500))
        ) as never as Promise<{ default: React.ComponentType }>
);

function LateSuspenseSibling(): React.JSX.Element | null {
    const [show, setShow] = useState(false);

    useEffect(() => {
        const timer = window.setTimeout(() => setShow(true), LATE_SUSPENSE_AT);
        return () => window.clearTimeout(timer);
    }, []);

    return show ? <LateChunk /> : null;
}

const PROJECT_UID = "fixtureproj";
const CARD_UID = "fixturecard";

const fixtureUser = AuthUser.Model.fromOne({
    uid: "fixture-user",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    type: "user",
    firstname: "Fixture",
    lastname: "User",
    email: "fixture@example.com",
    username: "fixture",
    api_key_role_actions: ["*"],
    setting_role_actions: ["*"],
    mcp_role_actions: ["*"],
    user_groups: [],
    subemails: [],
    preferred_lang: "en",
    notification_unsubs: {},
    industry: "",
    purpose: "",
} as never);

type TEvent = { type: string; t: number };
const events: TEvent[] = [];
const record = (type: string) => {
    events.push({ type, t: performance.now() });
};

let viewerMounted = 0;
let openAnimationStarts = 0;
const trackedViewers = new WeakSet<Element>();

document.addEventListener(
    "animationstart",
    (event) => {
        const target = event.target as Element;
        if (target instanceof Element && target.hasAttribute("data-card-viewer") && event.animationName === "card-viewer-open") {
            openAnimationStarts += 1;
            record("animationstart:card-viewer-open");
        }
    },
    true
);

function trackViewer(node: Element): void {
    if (!node.hasAttribute("data-card-viewer") || trackedViewers.has(node)) {
        return;
    }
    trackedViewers.add(node);
    viewerMounted += 1;
    record("viewer-mounted");
}

new MutationObserver((mutations) => {
    for (const mutation of mutations) {
        if (mutation.type === "attributes" && mutation.target instanceof Element) {
            trackViewer(mutation.target);
            continue;
        }
        for (const node of mutation.addedNodes) {
            if (node instanceof Element) {
                trackViewer(node);
                node.querySelectorAll?.("[data-card-viewer]").forEach(trackViewer);
            }
        }
        for (const node of mutation.removedNodes) {
            if (node instanceof Element && (trackedViewers.has(node) || !!node.querySelector?.("[data-card-viewer]"))) {
                trackedViewers.delete(node);
                record("viewer-unmounted");
            }
        }
    }
}).observe(document.documentElement, { childList: true, subtree: true, attributeFilter: ["data-card-viewer"] });

// Signed-in session restored before the board route renders.
useAuthStore.setState({ currentUser: fixtureUser, state: "loaded" });

const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity } },
});

function useHasCurrentUser(): boolean {
    const [hasUser, setHasUser] = useState(!!useAuthStore.getState().currentUser);

    useEffect(() => {
        const unsubscribe = useAuthStore.subscribe(() => {
            setHasUser(!!useAuthStore.getState().currentUser);
        });
        return unsubscribe;
    }, []);

    return hasUser;
}

/**
 * Mirrors BoardProxy: skeleton while the project summary is unavailable, then
 * BoardProxyDisplay with the same currentUser/project conditional and the same
 * key={pageRoute} the real board uses for the embedded card viewer.
 */
function CompactSummaryRefresh(): React.JSX.Element {
    const { refetch } = useGetCards({ project_uid: PROJECT_UID }, { enabled: false });
    return <Button onClick={() => void refetch()}>Refresh compact summary</Button>;
}

function BoardRouteFixture(): React.JSX.Element {
    const [projectReady, setProjectReady] = useState(false);
    const hasUser = useHasCurrentUser();
    const location = usePageLocation();
    const { projectUID } = useParams();
    const [, pageRoute] = location.pathname.split("/").slice(2);
    const isCardPage = !!pageRoute && !["graph", "wiki", "settings"].includes(pageRoute);

    useEffect(() => {
        const timer = window.setTimeout(() => setProjectReady(true), PROJECT_DELAY);
        return () => window.clearTimeout(timer);
    }, [PROJECT_DELAY]);

    if (!projectReady) {
        return <SkeletonBoard />;
    }

    const content = (
        <div
            className={cn(
                "flex h-[100dvh] w-full flex-col",
                query.has("workspaceRail") &&
                    "[--workbench-bottom-inset:calc(3.5rem+1px+env(safe-area-inset-bottom))] md:[--workbench-bottom-inset:0px]"
            )}
        >
            {query.has("workspaceHeader") && <Header navs={[]} title="Fixture workspace" compact />}
            {query.has("workspaceRail") && (
                <ActivityRail
                    items={[
                        { id: "explorer", icon: "panel-left", label: "Explorer", mobile: "primary", onClick: () => {} },
                        { id: "work", icon: "list-checks", label: "My Work", mobile: "primary", onClick: () => {} },
                    ]}
                />
            )}
            {query.has("compactSummary") && <CompactSummaryRefresh />}
            {hasUser ? (
                <div className="relative min-w-0 flex-1">
                    <div className="relative size-full" />
                    {!isCardPage && (
                        <Floating.Nav fixed items={[]} trailing={<CardFlipTray userUID={fixtureUser.uid} projectUID={projectUID ?? PROJECT_UID} />} />
                    )}
                    {isCardPage && <BoardCardPage key={pageRoute} projectUID={projectUID ?? PROJECT_UID} cardUID={pageRoute} embedded />}
                </div>
            ) : (
                <SkeletonBoard />
            )}
        </div>
    );
    return query.has("sidebar") ? (
        <ResizableSidebar initialWidth={280} defaultCollapsed={query.has("sidebarCollapsed")} floatingHidden main={content}>
            <p>Fixture Explorer</p>
        </ResizableSidebar>
    ) : (
        content
    );
}

function ScopedLocation(): React.JSX.Element {
    const location = usePageLocation();
    return query.has("scopedSearch") ? <output data-scoped-location>{location.pathname + location.search}</output> : <></>;
}

function ViewerNavigationScope({ children }: { children: React.ReactNode }) {
    return query.has("paneScope") ? (
        <PageNavigationScope basePath={`/board/${PROJECT_UID}`} initialEntry={`/board/${PROJECT_UID}/${CARD_UID}?filters=keyword:pane-only`}>
            {children}
        </PageNavigationScope>
    ) : (
        <>{children}</>
    );
}

function Harness(): React.JSX.Element {
    const tree = (
        <MemoryRouter
            initialEntries={[
                (query.has("fullDashboard")
                    ? "/dashboard/projects/all"
                    : query.has("fullGraph")
                      ? `/board/${PROJECT_UID}/graph`
                      : query.has("fullBoard")
                        ? `/board/${PROJECT_UID}`
                        : `/board/${PROJECT_UID}/${CARD_UID}`) + (query.has("scopedSearch") ? "?filters=keyword:pane-only" : ""),
            ]}
        >
            <ViewerNavigationScope>
                <QueryClientProvider client={queryClient}>
                    <PageHeaderProvider>
                        <AuthProvider>
                            <SocketProvider>
                                <BoardController>
                                    <Routes>
                                        <Route path="/dashboard/:pageType/:tabName?" element={<DashboardProxy />} />
                                        <Route path="/board/:projectUID" element={query.has("fullBoard") ? <BoardProxy /> : <BoardRouteFixture />} />
                                        <Route
                                            path="/board/:projectUID/:cardUID"
                                            element={query.has("fullGraph") || query.has("fullBoard") ? <BoardProxy /> : <BoardRouteFixture />}
                                        />
                                        <Route path="*" element={<div data-fixture-not-found="" />} />
                                    </Routes>
                                    <ScopedLocation />
                                    {LATE_SUSPENSE && <LateSuspenseSibling />}
                                </BoardController>
                            </SocketProvider>
                        </AuthProvider>
                    </PageHeaderProvider>
                </QueryClientProvider>
            </ViewerNavigationScope>
        </MemoryRouter>
    );

    if (!LATE_SUSPENSE) {
        return tree;
    }

    return <Suspense fallback={<div data-fixture-suspense-fallback="" />}>{tree}</Suspense>;
}

const root = createRoot(document.getElementById("root")!);
root.render(
    STRICT_MODE ? (
        <StrictMode>
            <Harness />
        </StrictMode>
    ) : (
        <Harness />
    )
);

// Spec-side readout (values keep updating; the spec polls them).
window.setInterval(() => {
    const globals = window as unknown as Record<string, unknown>;
    globals.__viewerMounted = viewerMounted;
    globals.__openAnimationStarts = openAnimationStarts;
    globals.__events = [...events];
    const authState = useAuthStore.getState();
    globals.__authState = { state: authState.state, hasUser: !!authState.currentUser };
}, 50);
