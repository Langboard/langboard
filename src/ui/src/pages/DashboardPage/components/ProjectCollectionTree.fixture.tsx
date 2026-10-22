import { createRoot } from "react-dom/client";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { PlateEditor } from "@/components/Editor/plate-editor";
import ProjectCollectionTree from "./ProjectCollectionTree";
import ProjectCompactItem from "./ProjectCompactItem";
import useProjectCollections from "./useProjectCollections";
import i18n from "@/i18n";
import "@/assets/styles/main.css";
await i18n.changeLanguage("en-US");
const actor = AuthUser.Model.fromOne({
    uid: "collection-tree",
    type: "user",
    firstname: "Tree",
    lastname: "Fixture",
    username: "tree",
    created_at: new Date(),
    updated_at: new Date(),
});
useAuthStore.setState({ currentUser: actor });
const projects = Project.Model.fromArray(
    ["Zulu", "Alpha", "New"].map((title, index) => ({
        uid: `0000000000${index + 1}`,
        title,
        owner_uid: actor.uid,
        project_type: "Other",
        created_at: new Date(),
        updated_at: new Date(),
        starred: false,
        current_auth_role_actions: [],
        all_members: [],
    }))
);
function Fixture() {
    const result = useProjectCollections(actor.uid);
    const [destination, navigate] = useState("");
    const [availableProjects, setAvailableProjects] = useState(projects);
    const [content, setContent] = useState("Editor and tree share DND");
    return (
        <main className="w-full max-w-sm p-2">
            <ProjectCompactItem project={projects[2]} updateStarredProjects={() => {}} />
            <button type="button" onClick={() => setAvailableProjects([])}>
                Remove board access
            </button>
            <ProjectCollectionTree
                collections={result.collections}
                projects={availableProjects}
                busy={result.busy}
                save={result.save}
                navigate={navigate}
            />
            <output className="sr-only" data-testid="collections" data-busy={result.busy}>
                {JSON.stringify(result.collections)}
            </output>
            <output className="sr-only" data-testid="destination">
                {destination}
            </output>
            <PlateEditor
                editorType="view"
                currentUser={actor}
                mentionables={[]}
                value={{ content }}
                setValue={(next) => setContent(next.content)}
                readOnly={false}
                placeholder="Fixture editor"
            />
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
