import { createContext, useContext, useEffect, useState, useRef, Suspense } from "react";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { AuthProvider } from "@/core/providers/AuthProvider";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, Link, RouterProvider, useLocation, MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WorkbenchRouteLayout from "./WorkbenchRouteLayout";
import DashboardStyledLayout from "./DashboardStyledLayout";
import { LabelBadge } from "@/components/LabelBadge";
import DropdownMenu from "@/components/base/DropdownMenu";
import Floating from "@/components/base/Floating";
import useAuthStore from "@/core/stores/AuthStore";
import Dialog from "@/components/base/Dialog";
import { BoardCardSectionSaveProvider } from "@/pages/BoardPage/components/card/BoardCardSectionSaveProvider";
import BoardCard from "@/pages/BoardPage/components/card/BoardCard";
import i18n from "@/i18n";
import { AuthUser } from "@/core/models";
import "@/i18n";
import "@/assets/styles/main.css";
import "@/core/injection";

const selectFixtureUser = (uid: string) => {
    useAuthStore.setState({
        state: "loaded",
        pageLoaded: true,
        currentUser: AuthUser.Model.fromOne({ uid, created_at: new Date(), updated_at: new Date() }),
    });
};
selectFixtureUser("sidebar-user-a");

const PageContext = createContext("missing context");
function SidebarContent() {
    const [search, setSearch] = useState("");
    return (
        <>
            <p>{useContext(PageContext)}</p>
            <LabelBadge compact name="🧩 Contract" emoji="🧩" color="#8B5CF6" />
            <input aria-label="Sidebar search" value={search} onChange={(event) => setSearch(event.currentTarget.value)} />
            <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                    <button type="button">Nested menu</button>
                </DropdownMenu.Trigger>
                <DropdownMenu.Content>
                    <DropdownMenu.Item>Nested item</DropdownMenu.Item>
                </DropdownMenu.Content>
            </DropdownMenu.Root>
            <Link to="/board/fixture/card">Sidebar open Card</Link>
        </>
    );
}
let pageMounts = 0;
function Page({ name }: { name: string }) {
    const location = useLocation();
    const [mountNumber, setMountNumber] = useState(0);
    useEffect(() => setMountNumber(++pageMounts), []);
    const isMobile = window.innerWidth < 768;
    const [contextOpen, setContextOpen] = useState(false);
    return (
        <PageContext.Provider value={`Sidebar ${name}`}>
            <DashboardStyledLayout
                headerNavs={[]}
                headerTitle={name}
                activityRailItems={[
                    { icon: "panel-left", label: "Explorer", active: isMobile ? contextOpen : true, onClick: () => setContextOpen((open) => !open) },
                    { icon: "inbox", label: "Inbox", onClick: () => setContextOpen(true) },
                    { icon: "network", label: "Relations", onClick: () => setContextOpen(true) },
                    { icon: "key", label: "Hidden action", hidden: true, onClick: () => {} },
                    { icon: "settings", label: "App settings", onClick: () => {}, placement: "bottom" },
                ]}
                workbenchContext={<SidebarContent />}
                workbenchContextHidden={isMobile}
                mobileWorkbenchContext={
                    isMobile && contextOpen ? { title: "Explorer", icon: "panel-left", onClose: () => setContextOpen(false) } : undefined
                }
            >
                <h1>{name}</h1>
                <Floating.Nav fixed items={[{ label: "Work dock", icon: "hammer" }]} />
                <output data-testid="page-mounts">{mountNumber}</output>
                <p>{location.pathname}</p>
                <nav className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => selectFixtureUser("sidebar-user-a")}>
                        Use first user
                    </button>
                    <button type="button" onClick={() => selectFixtureUser("sidebar-user-b")}>
                        Use second user
                    </button>
                    <Link to="/dashboard/projects/all">Open Dashboard</Link>
                    <Link to="/board/fixture" state={{ commandPaletteFocus: true }}>
                        Open Board
                    </Link>
                    <Link to="/board/fixture/card">Open Card</Link>
                    <Link to="/board/fixture/wiki">Open Wiki</Link>
                </nav>
            </DashboardStyledLayout>
        </PageContext.Provider>
    );
}
const router = createMemoryRouter(
    [
        {
            element: <WorkbenchRouteLayout />,
            children: [
                { path: "/dashboard/projects/all", element: <Page name="Dashboard" /> },
                { path: "/board/fixture", element: <Page name="Board" /> },
                { path: "/board/fixture/card", element: <Page name="Card" /> },
                { path: "/board/fixture/wiki", element: <Page name="Wiki" /> },
            ],
        },
    ],
    { initialEntries: ["/dashboard/projects/all"] }
);
function LensFixture() {
    const viewportRef = useRef<HTMLDivElement>(null);
    const [cardUID, setCardUID] = useState("fixture-card");
    const [flippedUID, setFlippedUID] = useState("");
    const currentUser = useAuthStore((state) => state.currentUser)!;
    return (
        <MemoryRouter>
            <AuthProvider>
                <DndProvider backend={HTML5Backend}>
                    <main className="h-screen p-2" ref={viewportRef}>
                        <output data-testid="flipped-card">{flippedUID}</output>
                        <button onClick={() => setCardUID((uid) => (uid === "fixture-card" ? "other-card" : "fixture-card"))}>Switch card</button>
                        <button onClick={() => selectFixtureUser(currentUser.uid === "sidebar-user-a" ? "sidebar-user-b" : "sidebar-user-a")}>
                            Switch account
                        </button>
                        <Dialog.Root open modal={false}>
                            <div role="dialog" className="flex h-[90vh] max-w-6xl flex-col p-1">
                                <BoardCardSectionSaveProvider>
                                    <BoardCard
                                        projectUID="fixture"
                                        cardUID={cardUID}
                                        currentUser={currentUser}
                                        viewportRef={viewportRef}
                                        isExpanded
                                        onClose={() => {}}
                                        onFlip={(card) => setFlippedUID(card.uid)}
                                    />
                                </BoardCardSectionSaveProvider>
                            </div>
                        </Dialog.Root>
                    </main>
                </DndProvider>
            </AuthProvider>
        </MemoryRouter>
    );
}
const params = new URLSearchParams(location.search);
void i18n.changeLanguage(params.get("lang") ?? "en-US").then(() =>
    createRoot(document.getElementById("root")!).render(
        <Suspense>
            <QueryClientProvider client={new QueryClient()}>
                {params.has("lenses") ? <LensFixture /> : <RouterProvider router={router} />}
            </QueryClientProvider>
        </Suspense>
    )
);
