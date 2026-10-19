import { lazy, Suspense, useReducer, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Project, ProjectColumn } from "@/core/models";
import Discovery from "./ProjectDiscoveryPage";
import type { IGetProjectsResponse } from "@/controllers/api/dashboard/useGetProjects";
import "@/i18n";
import "@/assets/styles/main.css";
const baselinePath = "/.verification/project-discovery-20261019/ProjectDiscoveryPage.before.tsx";
const Baseline = lazy(() => import(/* @vite-ignore */ baselinePath));
const useBaseline = new URLSearchParams(location.search).has("baseline");
const date = (day: number) => new Date(`2026-10-${String(day).padStart(2, "0")}T00:00:00Z`);
const records = Array.from({ length: 11 }, (_, index) => ({
    uid: `project-${index}`,
    title: `Shared Project ${index}`,
    owner_uid: "owner",
    project_type: "SW",
    created_at: date(1),
    updated_at: date(1),
    starred: index < 5,
    related_to_current_user: index === 0 || index === 5,
    board_has_unread_change: index === 0 || index === 6,
    last_activity_at: index === 10 ? null : date(index + 1),
    last_viewed_at: date(19 - index),
    all_members: [],
    current_auth_role_actions: [],
}));
const models = Project.Model.fromArray(records);
const columns = ProjectColumn.Model.fromArray(
    models.map((project, index) => ({
        created_at: date(1),
        updated_at: date(1),
        uid: `column-${index}`,
        project_uid: project.uid,
        name: "Doing",
        order: index,
        is_archive: false,
        count: 2,
        incomplete_count: index === 0 || index === 5 ? 2 : 0,
        workflow_stage: "active",
    }))
);
// A loaded model outside the authoritative response must never become a search result.
Project.Model.fromOne({ ...records[0], uid: "forbidden", title: "Shared forbidden" });
function Fixture() {
    const [userUID, setUserUID] = useState("discovery-a");
    const [data, setData] = useState<IGetProjectsResponse>({ projects: [...models, models[0]], columns });
    const updater = useReducer((value) => value + 1, 0);
    const Page = useBaseline ? Baseline : Discovery;
    return (
        <main id="main" className="h-screen overflow-y-auto bg-background px-2 pb-16 text-foreground">
            <nav className="flex flex-wrap gap-3">
                <button
                    onClick={() =>
                        setData({
                            projects: [
                                ...models,
                                ...Project.Model.fromArray(
                                    Array.from({ length: 22 }, (_, index) => ({
                                        ...records[0],
                                        uid: `extra-${index}`,
                                        title: `Extra Project ${index}`,
                                        starred: false,
                                        related_to_current_user: false,
                                    }))
                                ),
                            ],
                            columns,
                        })
                    }
                >
                    Grow projects
                </button>
                <button onClick={() => setData({ projects: models, columns })}>Shrink projects</button>
                <button onClick={() => setUserUID((uid) => (uid === "discovery-a" ? "discovery-b" : "discovery-a"))}>Switch account</button>
                <button onClick={() => setData({ projects: models.filter((project) => project.uid !== "project-0"), columns })}>
                    Revoke first project
                </button>
                <button onClick={() => Project.Model.fromOne({ ...records[0], board_has_unread_change: false }, true)}>Mark first read</button>
                <button
                    onClick={() =>
                        Project.Model.fromOne({ ...records[7], starred: true, related_to_current_user: true, title: "Renamed Project" }, true)
                    }
                >
                    Change project data
                </button>
                <button
                    onClick={() =>
                        ProjectColumn.Model.getModels((column) => column.uid === "column-5").forEach((column) => {
                            column.incomplete_count = 0;
                        })
                    }
                >
                    Finish related work
                </button>
            </nav>
            <Suspense fallback={<span>Loading baseline</span>}>
                <Page
                    userUID={userUID}
                    projectsData={data}
                    isProjectsFetching={false}
                    isProjectsLoading={false}
                    updateStarredProjects={updater[1]}
                    scrollAreaUpdater={updater}
                />
            </Suspense>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
