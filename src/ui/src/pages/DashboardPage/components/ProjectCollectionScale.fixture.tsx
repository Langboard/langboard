import "@/core/injection";
import "@/i18n";
import "@/assets/styles/main.css";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import { Project } from "@/core/models";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Profiler, useRef, useState, type ProfilerOnRenderCallback } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import ProjectCollectionTree from "@/pages/DashboardPage/components/ProjectCollectionTree";
import type { ProjectCollection } from "@/pages/DashboardPage/components/useProjectCollections";

const projects = Project.Model.fromArray(
    Array.from({ length: 128 }, (_, index) => ({
        uid: `0SCL${String(index).padStart(7, "0")}`,
        title: `Scale board ${String(index).padStart(3, "0")}`,
        owner_uid: "scale-owner",
        project_type: "Other",
        created_at: new Date("2026-01-01T00:00:00Z"),
        updated_at: new Date("2026-01-01T00:00:00Z"),
        starred: false,
        current_auth_role_actions: [],
        all_members: [],
    }))
);
projects.forEach((project) => Project.Model.unsubscribeSocketEvents(project.uid));

// Match the persisted contract: at most 64 collections and 1,024 references.
const initialCollections: ProjectCollection[] = Array.from({ length: 64 }, (_, index) => ({
    uid: `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    name: `Scale collection ${String(index).padStart(3, "0")}`,
    board_uids: Array.from({ length: 16 }, (_, offset) => projects[(index * 16 + offset) % projects.length].uid).reverse(),
}));

type Commit = Parameters<ProfilerOnRenderCallback>;

function Fixture() {
    const [collections, setCollections] = useState(initialCollections);
    const [availableProjects, setAvailableProjects] = useState(projects);
    const [destination, setDestination] = useState("");
    const [samples, setSamples] = useState<Commit[]>([]);
    const commits = useRef<Commit[]>([]);
    const recordCommit: ProfilerOnRenderCallback = (...sample) => {
        commits.current.push(sample);
        if (commits.current.length > 100) commits.current.shift();
    };

    return (
        <Flex direction="col" gap="3" p="3">
            <h1>Project collection scale fixture</h1>
            <p>Memory dataset: 64 collections and 1,024 board references.</p>
            <Flex wrap gap="2">
                <Button type="button" onClick={() => setAvailableProjects([])}>
                    Remove fixture board access
                </Button>
                <Button type="button" onClick={() => setAvailableProjects(projects)}>
                    Restore fixture board access
                </Button>
                <Button type="button" onClick={() => setSamples([...commits.current])}>
                    Snapshot render profile
                </Button>
            </Flex>
            <Profiler id="project-collection-tree" onRender={recordCommit}>
                <ProjectCollectionTree
                    collections={collections}
                    projects={availableProjects}
                    busy={false}
                    save={async (next) => setCollections(next)}
                    navigate={setDestination}
                />
            </Profiler>
            <output data-testid="scale-destination">{destination}</output>
            <output className="sr-only" data-testid="scale-profile">
                {JSON.stringify(samples)}
            </output>
        </Flex>
    );
}

createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
