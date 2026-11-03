import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useTranslation } from "react-i18next";
import { AuthUser, MetadataModel, Project, ProjectCard, User } from "@/core/models";
import CardTypeBadges from "@/components/CardTypeBadges";
import { applyMetadataUpdated, applyMetadataDeleted } from "@/controllers/socket/shared/MetadataSocketHelper";
import CardPresentationBadge from "@/components/CardPresentationBadge";
import { CARD_PRESENTATION_KEY, CARD_VISIBILITY_PRESENTATIONS } from "./CardPresentation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { BoardProvider } from "@/core/providers/BoardProvider";
import { BoardCardProvider, useBoardCard } from "@/core/providers/BoardCardProvider";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import BoardColumnCardCollapsible from "@/pages/BoardPage/components/board/BoardColumnCardCollapsible";
import BoardCardMemberList from "@/pages/BoardPage/components/card/BoardCardMemberList";
import { api } from "@/core/helpers/Api";
import "@/i18n";
import "@/assets/styles/main.css";
const visibilityProject = Project.Model.fromOne({ uid: "visibility-project", all_members: [], created_at: new Date(), updated_at: new Date() });
const visibilityCard = ProjectCard.Model.fromOne({
    uid: "visibility-card",
    project_uid: visibilityProject.uid,
    visibility: "INTERNAL",
    created_at: new Date(),
    updated_at: new Date(),
});
const privateCard = ProjectCard.Model.fromOne({
    uid: "private-card",
    project_uid: visibilityProject.uid,
    visibility: "PRIVATE",
    created_at: new Date(),
    updated_at: new Date(),
});
const externalMember = User.Model.fromOne({
    uid: "external-member",
    firstname: "External",
    lastname: "Member",
    membership_classification: "external" as const,
    type: "user",
    created_at: new Date(),
    updated_at: new Date(),
});
function VisibilityFixture() {
    const visibility = visibilityCard.useField("visibility");
    return (
        <section className="mt-8" aria-label="Member transitions">
            <div data-testid="internal-card">
                <CardTypeBadges card={visibilityCard} />
            </div>
            <div data-testid="private-card">
                <CardTypeBadges card={privateCard} />
            </div>
            <output data-testid="stored-visibility">{visibility}</output>
            <button
                onClick={() => {
                    visibilityProject.all_members = [externalMember];
                }}
            >
                Add external member
            </button>
            <button
                onClick={() =>
                    User.Model.fromOne({
                        uid: externalMember.uid,
                        type: "user" as const,
                        firstname: "External",
                        lastname: "Member",
                        created_at: new Date(),
                        updated_at: new Date(),
                        membership_classification: "internal",
                    })
                }
            >
                Classify member as internal
            </button>
            <button
                onClick={() =>
                    User.Model.fromOne({
                        uid: externalMember.uid,
                        type: "user" as const,
                        firstname: "External",
                        lastname: "Member",
                        created_at: new Date(),
                        updated_at: new Date(),
                        membership_classification: "external",
                    })
                }
            >
                Classify member as external
            </button>
            <button
                onClick={() => {
                    visibilityProject.all_members = [];
                }}
            >
                Remove external member
            </button>
        </section>
    );
}
const appCard = ProjectCard.Model.fromOne({ uid: "app-card", project_uid: "test-project", created_at: new Date(), updated_at: new Date() });
MetadataModel.Model.fromOne({ uid: appCard.uid, type: "card", metadata: {}, created_at: new Date(), updated_at: new Date() });
const appPresentation = {
    version: 1,
    key: "app.github.issue",
    axis: "origin",
    name: "GitHub issue",
    description: "App-reported origin.",
    icon: "🔗",
    translations: { "ko-KR": { name: "깃허브 이슈", description: "앱에서 제공한 출처입니다." } },
};
const roleBase = { created_at: new Date(), updated_at: new Date() };
const owner = User.Model.fromOne({
    ...roleBase,
    uid: "role-owner",
    type: "user",
    firstname: "Assigned",
    lastname: "Owner",
    email: "owner@example.test",
    membership_classification: "internal",
});
const worker = User.Model.fromOne({
    ...roleBase,
    uid: "role-worker",
    type: "user",
    firstname: "Checklist",
    lastname: "Worker",
    email: "worker@example.test",
    membership_classification: "internal",
});
const outsider = User.Model.fromOne({ ...roleBase, uid: "role-outsider", type: "user", firstname: "Outside", lastname: "Scope" });
const roleUser = AuthUser.Model.fromOne({ ...roleBase, uid: "role-user", firstname: "Viewer", type: "user", user_groups: [], is_admin: true });
const roleProject = Project.Model.fromOne({
    ...roleBase,
    uid: "role-project",
    all_members: [owner, worker],
    labels: [],
    current_auth_role_actions: ["*"],
});
const roleCard = ProjectCard.Model.fromOne({
    ...roleBase,
    uid: "role-card",
    project_uid: roleProject.uid,
    title: "Native responsibility roles",
    visibility: "INTERNAL",
    member_uids: [owner.uid],
    active_workers: [],
    project_members: [owner, worker],
    current_auth_role_actions: ["*"],
    labels: [],
    relationships: [],
});
function setRoleWorker(status: "started" | "paused", uid = worker.uid) {
    ProjectCard.Model.fromOne<typeof ProjectCard.Model, ProjectCard.IStore>({
        ...roleBase,
        uid: roleCard.uid,
        active_workers: [
            {
                user_uid: uid,
                status,
                started_at: null,
                elapsed_seconds: 60,
                checkitem_uid: "role-item",
                title: "Inspect checklist",
                checkitems: [
                    {
                        uid: "role-item",
                        title: "Inspect checklist",
                        status,
                        started_at: null,
                        elapsed_seconds: 60,
                        sampled_at: new Date().toISOString(),
                    },
                ],
            },
        ],
    });
}
api.defaults.adapter = async (config) => {
    const data = JSON.parse(config.data);
    document.querySelector('[data-testid="assignment-request"]')!.textContent = JSON.stringify(data);
    roleCard.member_uids = data.assigned_users;
    return { data: {}, status: 200, statusText: "OK", headers: {}, config };
};
function RoleDetail() {
    const { enterCardEditMode, leaveCardEditMode } = useBoardCard();
    return (
        <>
            <button onClick={enterCardEditMode}>Edit assignments</button>
            <button onClick={leaveCardEditMode}>View assignments</button>
            <BoardCardMemberList />
        </>
    );
}
function RoleFixture() {
    const viewportRef = useRef<HTMLDivElement>(null);
    return (
        <section aria-label="Native responsibility fixture" className="mt-8 max-w-sm" ref={viewportRef}>
            <button
                onClick={() => {
                    User.Model.fromOne({
                        ...roleBase,
                        uid: owner.uid,
                        type: "user",
                        firstname: owner.firstname,
                        lastname: owner.lastname,
                        membership_classification: owner.membership_classification === "internal" ? "external" : "internal",
                    });
                }}
            >
                Toggle assigned member classification
            </button>
            <label>
                Role scenario
                <select
                    aria-label="Role scenario"
                    defaultValue="assignee"
                    onChange={(event) => {
                        const scenario = event.target.value;
                        roleCard.visibility = scenario === "private" ? "PRIVATE" : "INTERNAL";
                        roleCard.member_uids = ["worker", "none"].includes(scenario) ? [] : [owner.uid];
                        ProjectCard.Model.fromOne<typeof ProjectCard.Model, ProjectCard.IStore>({
                            ...roleBase,
                            uid: roleCard.uid,
                            active_workers: [],
                        });
                        if (["worker", "both", "paused", "private"].includes(scenario)) setRoleWorker(scenario === "paused" ? "paused" : "started");
                        if (scenario === "same") setRoleWorker("started", owner.uid);
                        if (scenario === "outsider") {
                            roleCard.member_uids = [outsider.uid];
                            setRoleWorker("started", outsider.uid);
                        }
                    }}
                >
                    {["assignee", "worker", "both", "paused", "same", "none", "private", "outsider"].map((value) => (
                        <option key={value}>{value}</option>
                    ))}
                </select>
            </label>
            <div data-testid="native-role-board">
                <BoardProvider project={roleProject} currentUser={roleUser}>
                    <ModelRegistry.ProjectCard.Provider model={roleCard} params={{ setFilters: () => {} }}>
                        <BoardColumnCardCollapsible isDragging={false} />
                    </ModelRegistry.ProjectCard.Provider>
                </BoardProvider>
            </div>
            <div data-testid="native-role-detail">
                <BoardCardProvider projectUID={roleProject.uid} card={roleCard} currentUser={roleUser} viewportRef={viewportRef}>
                    <RoleDetail />
                </BoardCardProvider>
            </div>
            <output data-testid="assignment-request" className="block break-all" />
        </section>
    );
}
function Fixture() {
    const [t, i18n] = useTranslation();
    const [showVisibility, setShowVisibility] = useState(true);
    const definition = CARD_VISIBILITY_PRESENTATIONS.whisper;
    return (
        <main className="p-6">
            <label>
                Language
                <select aria-label="Language" value={i18n.language} onChange={(event) => void i18n.changeLanguage(event.target.value)}>
                    {["en-US", "ko-KR", "ja-JP", "zh-CN"].map((language) => (
                        <option key={language}>{language}</option>
                    ))}
                </select>
            </label>
            <section className="mt-8">
                <CardPresentationBadge
                    presentationKey={definition.key}
                    axis="visibility"
                    icon={definition.icon}
                    name={t(definition.nameKey)}
                    description={t(definition.descriptionKey)}
                />
                <CardTypeBadges card={appCard} />
                <button
                    onClick={() => applyMetadataUpdated("card", appCard.uid, { key: CARD_PRESENTATION_KEY, value: JSON.stringify(appPresentation) })}
                >
                    Attach app type
                </button>
                <button onClick={() => applyMetadataDeleted("card", appCard.uid, { keys: [CARD_PRESENTATION_KEY] })}>Remove app type</button>
            </section>
            <button onClick={() => setShowVisibility((value) => !value)}>Toggle member view</button>
            <button
                onClick={() =>
                    User.Model.fromOne({
                        uid: externalMember.uid,
                        type: "user" as const,
                        firstname: "External",
                        lastname: "Member",
                        created_at: new Date(),
                        updated_at: new Date(),
                        membership_classification: "external",
                    })
                }
            >
                Update hidden member classification
            </button>
            {showVisibility && <VisibilityFixture />}
            <RoleFixture />
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
            <Fixture />
        </QueryClientProvider>
    </MemoryRouter>
);
