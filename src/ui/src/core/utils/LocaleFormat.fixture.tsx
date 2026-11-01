import { getMcpToolStore, useMcpTool } from "@/core/stores/McpToolStore";
import { getBotStatusStore, useHasRunningBot } from "@/core/stores/BotStatusStore";
import { getEditorStore, useIsCurrentEditor } from "@/core/stores/EditorStore";
import { getUserSettingsStore, useUserSettings } from "@/core/stores/UserSettingsStore";
import { BoardProvider } from "@/core/providers/BoardProvider";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import BoardColumnCardCollapsible from "@/pages/BoardPage/components/board/BoardColumnCardCollapsible";
import CardWorkbenchLenses from "@/pages/BoardPage/components/card/CardWorkbenchLenses";
import ApiKeyIpWhitelist from "@/pages/SettingsPage/components/apiKeys/ApiKeyIpWhitelist";
import { ApiKeySettingModel } from "@/core/models";
import { AuthProvider } from "@/core/providers/AuthProvider";
import useAuthStore from "@/core/stores/AuthStore";
import { AuthUser, MetadataModel } from "@/core/models";
import { DOCLING_DOCUMENTS_METADATA_KEY } from "@/core/constants/DoclingMetadata";
import { BoardCardAttachmentDocumentProgress } from "@/pages/BoardPage/components/card/attachment/BoardCardAttachmentDocument";
import CustomSelect from "@/components/Cron/CustomSelect";
import { PlaceholderPlugin } from "@platejs/media/react";
import { EditorDataProvider } from "@/core/providers/EditorDataProvider";
import { ImageProgress, PlaceholderElement } from "@/components/plate-ui/media-placeholder-node";
import { UNITS } from "@/components/Cron/constants";
import OllamaModelTracker from "@/pages/SettingsPage/components/ollama/OllamaModelTracker";
import { getOllamaModelStore, updateProgressCallback } from "@/core/stores/OllamaModelStore";
import { DescriptionOverviewRail } from "@/pages/BoardPage/components/card/description/DescriptionOverviewRail";
import DataTablePagination from "@/components/base/DataTable/Pagination";
import { DataTableProvider } from "@/components/base/DataTable/Provider";
import { createPlateEditor, Plate } from "platejs/react";
import { CodeBlockKit } from "@/components/Editor/plugins/code-block-kit";
import { Editor } from "@/components/plate-ui/editor";
import CardContentBlockList from "@/pages/BoardPage/components/card/CardContentBlockList";
import RelationshipTypePicker from "@/pages/BoardPage/components/board/RelationshipTypePicker";
import { GlobalRelationshipType } from "@/core/models";
import BoardWorkIsland from "@/pages/BoardPage/components/board/BoardWorkIsland";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { Project, ProjectCard } from "@/core/models";
import type { IWorkState } from "@/core/models/ProjectCard";
import BoardOutlineSidebar from "@/pages/BoardPage/components/board/BoardOutlineSidebar";
import { api } from "@/core/helpers/Api";
import { useRef, useState } from "react";
import Breadcrumb from "@/components/base/Breadcrumb";
import Dialog from "@/components/base/Dialog";
import MarkdownThinkBlock from "@/components/Markdown/ThinkBlock";
import Calendar from "@/components/base/Calendar";
import { createRoot } from "react-dom/client";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import "@/core/injection/StringExtensions";
import useUpdateDateDistance from "@/core/hooks/useUpdateDateDistance";
import { formatDateTime, formatTimerDuration, formatNumber, formatFileSize } from "./LocaleFormat";
import { SUPPORTED_LOCALES } from "./LocalePolicy";

api.defaults.adapter = async (config) => ({ status: 200, statusText: "OK", headers: {}, config, data: { active_work: [] } });
getOllamaModelStore().replacePullingModels([{ name: "locale-progress", progress: 12.34, isTracking: true }]);
const fixtureProject = Project.Model.fromOne({
    uid: "locale-fixture",
    created_at: new Date(),
    updated_at: new Date(),
    title: "Locale board",
    all_members: [],
    labels: [],
    current_auth_role_actions: ["*"],
});
const fixtureRelationships = ["contains", "blocks", "references"].map((machine_semantic) => ({
    uid: machine_semantic,
    machine_semantic,
    is_active: true,
    parent_name: `User ${machine_semantic}`,
    child_name: `User ${machine_semantic}`,
})) as GlobalRelationshipType.TModel[];
const cronEveryFive = Array.from({ length: 12 }, (_, index) => index * 5);
const date = new Date(Date.now() - 300000);
const uploadingImage = new File([new Uint8Array([0])], "locale-upload.png", { type: "image/png" });
const railChunks = Array.from({ length: 1234 }, (_, index) => ({
    id: `rail-${index}`,
    content: `User block ${index}`,
    metadata: { type: "paragraph" as const, previewText: `User block ${index}`, textLength: 12, isHeavy: false },
}));
ProjectCard.Model.fromOne({
    uid: "locale-outline",
    project_uid: "locale-fixture",
    project_column_uid: "locale-column",
    title: "User card title",
    description: "",
    order: 0,
    created_at: new Date(),
    updated_at: new Date(),
    checklist_total_count: 2345,
    checklist_completed_count: 1234,
    count_comment: 1234,
    relationships: [],
});
const documentUser = AuthUser.Model.fromOne({ uid: "locale-document-user", created_at: new Date(), updated_at: new Date() });
useAuthStore.setState({ currentUser: documentUser, state: "loaded", pageLoaded: true });
MetadataModel.Model.fromOne({
    uid: "locale-document",
    type: "card",
    created_at: new Date(),
    updated_at: new Date(),
    metadata: {
        [DOCLING_DOCUMENTS_METADATA_KEY]: JSON.stringify([
            {
                attachment_uid: "locale-attachment",
                status: "processing",
                completed_pages: 1234,
                total_pages: 2345,
                progress_percent: 12.4,
                content: {},
            },
        ]),
    },
});
const localeApiKey = ApiKeySettingModel.Model.fromOne({
    uid: "locale-api-key",
    created_at: new Date(),
    updated_at: new Date(),
    ip_whitelist: Array.from({ length: 1235 }, () => "192.0.2.1"),
});
function PlaceholderFixture() {
    const [editor] = useState(() =>
        createPlateEditor({
            plugins: [PlaceholderPlugin.configure({ render: { node: PlaceholderElement } })],
            value: ["audio", "file", "img", "video"].map((mediaType) => ({
                type: PlaceholderPlugin.key,
                mediaType,
                id: `fixture-${mediaType}`,
                children: [{ text: "" }],
            })),
        })
    );
    return (
        <EditorDataProvider currentUser={documentUser} mentionables={[]} editorType="view">
            <Plate editor={editor}>
                <Editor />
            </Plate>
        </EditorDataProvider>
    );
}

getMcpToolStore().replaceTools([
    { name: "first", description: "First tool", input_schema: {} },
    { name: "second", description: "Second tool", input_schema: {} },
]);
getBotStatusStore().replaceMap({ card: { first: ["bot"] }, project_column: { second: [] } });
getEditorStore().setCurrentEditor("second");
getUserSettingsStore().updateSettings({ notifications_time_range: "7d", graph_view_modes: { fixture: "network" } });

function StoreSelectionFixture() {
    const [second, setSecond] = useState(false);
    const name = second ? "second" : "first";
    const tool = useMcpTool(name);
    const running = useHasRunningBot({ type: second ? "project_column" : "card", targetUID: name });
    const currentEditor = useIsCurrentEditor(name);
    const setting = useUserSettings(second ? "graph_view_modes" : "notifications_time_range");
    return (
        <section data-testid="store-selection">
            <output data-testid="selected-tool">{tool?.description}</output>
            <output data-testid="selected-running">{String(running)}</output>
            <output data-testid="selected-editor">{String(currentEditor)}</output>
            <output data-testid="selected-setting">{JSON.stringify(setting)}</output>
            <button onClick={() => setSecond(!second)}>Switch stored target</button>
        </section>
    );
}

function Fixture() {
    const [t, language] = useTranslation();
    const railViewport = useRef<HTMLDivElement>(null);
    const [pullName, setPullName] = useState("locale-progress");
    const [dragging, setDragging] = useState(false);
    const [editor] = useState(() =>
        createPlateEditor({
            plugins: [...CodeBlockKit],
            value: [{ type: "code_block", lang: "plaintext", children: [{ type: "code_line", children: [{ text: "const user = 1;" }] }] }],
        })
    );
    const distance = useUpdateDateDistance(date);
    return (
        <>
            <StoreSelectionFixture />
            <div data-testid="media-placeholders">
                <PlaceholderFixture />
            </div>
            <div data-testid="code-editor">
                <Plate editor={editor}>
                    <Editor aria-label="fixture code editor" />
                </Plate>
            </div>
            <div data-testid="content-blocks">
                <CardContentBlockList
                    blocks={[
                        {
                            block_uid: "source",
                            revision: 0,
                            updated_at: null,
                            order: 0,
                            type: "diagram",
                            payload: { engine: "mermaid", source: "graph TD; User--&gt;Data;", view_mode: "source" },
                        },
                        {
                            block_uid: "empty",
                            revision: 0,
                            updated_at: null,
                            order: 1,
                            type: "diagram",
                            payload: { engine: "mermaid", source: "", view_mode: "image" },
                        },
                    ]}
                />
            </div>
            <div data-testid="relationship-picker">
                <RelationshipTypePicker types={fixtureRelationships} isParent onSelect={() => {}} />
            </div>
            <div data-testid="work-island">
                <BoardWorkIsland project={fixtureProject} dragging={dragging} />
            </div>
            <input type="checkbox" aria-label="fixture dragging" checked={dragging} onChange={(event) => setDragging(event.target.checked)} />
            <div data-testid="common-ui">
                <Breadcrumb.Root>
                    <Breadcrumb.List>
                        <Breadcrumb.Item>
                            <Breadcrumb.Ellipsis />
                        </Breadcrumb.Item>
                    </Breadcrumb.List>
                </Breadcrumb.Root>
                <Dialog.Root>
                    <Dialog.CloseButton />
                </Dialog.Root>
                <MarkdownThinkBlock>Fixture content</MarkdownThinkBlock>
            </div>
            <div data-testid="outline-counts">
                <BoardOutlineSidebar cardUID="locale-outline" onRelations={() => {}} />
            </div>
            <div data-testid="pagination-counts">
                <DataTableProvider totalRecords={2345} itemsPerPage={1234}>
                    <DataTablePagination />
                </DataTableProvider>
            </div>
            <output data-testid="numeric-notification">{t("notification.{count} notifications received", { count: 1234 })}</output>
            <output data-testid="numeric-activity">{t("activity.{count} New Activities", { count: 1234 })}</output>
            <output data-testid="numeric-approval">{t("bot.{count} pending approvals", { count: 1234 })}</output>
            <div data-testid="description-rail" className="relative h-96">
                <div ref={railViewport} style={{ height: 320, overflowY: "auto" }}>
                    <div style={{ height: railChunks.length * 20 }} />
                </div>
                <DescriptionOverviewRail
                    chunks={railChunks}
                    activeIndex={1233}
                    onNavigate={() => {}}
                    viewportHeight={320}
                    scrollParentRef={railViewport}
                />
            </div>
            <output data-testid="relative">{distance}</output>
            <output data-testid="remaining-counts">
                {[
                    "settings.Partial Admin ({{count}} permissions)",
                    "settings.Partial access ({{count}} permissions)",
                    "settings.Selected event count",
                    "dashboard.Showing recent work",
                    "card.Seen by count",
                    "mcp.Available Tools ({count})",
                    "mcp.{count} tools",
                ].map((key) => (
                    <span key={key} data-count-key={key}>
                        {t(key, { count: 1234 })}
                    </span>
                ))}
            </output>
            <output data-testid="workflow-stage-label">{t("board.Workflow stage display", { stage: "User stage" })}</output>
            <output data-testid="graph-counts">
                {t("board.{cards} cards, {relationships} relationships", { cards: 1234, relationships: 2345 })}
            </output>
            <output data-testid="checklist-counts">{t("card.Checklist progress", { completed: 1234, total: 2345 })}</output>
            <output data-testid="stale-one">{t("card.Unchanged for {{days}} days", { days: 1, count: 1 })}</output>
            <output data-testid="stale-many">{t("card.Unchanged for {{days}} days", { days: 1234, count: 1234 })}</output>
            <output data-testid="overdue-one">{t("card.Overdue by {{count}} day", { count: 1 })}</output>
            <output data-testid="overdue-other">{t("card.Overdue by {{count}} day", { count: 2 })}</output>
            <div data-testid="cron-interval">
                <CustomSelect
                    value={cronEveryFive}
                    unit={UNITS[0]}
                    setValue={() => {}}
                    disabled
                    readOnly={false}
                    periodicityOnDoubleClick={false}
                    period="minute"
                    mode="multiple"
                />
            </div>
            <div data-testid="ollama-progress">
                <OllamaModelTracker name={pullName} />
                <button
                    onClick={() => {
                        getOllamaModelStore().upsertPullingModel({ name: "other-progress", progress: 67, isTracking: true });
                        setPullName("other-progress");
                    }}
                >
                    Switch pull target
                </button>
                <button
                    onClick={() =>
                        updateProgressCallback({ onSuccess: () => {}, onError: () => {} })({ model: pullName, status: "downloading", percent: 0 })
                    }
                >
                    Reset pull progress
                </button>
            </div>
            <output data-testid="duration">{formatTimerDuration({ hours: 1, minutes: 2, seconds: 3 }, language.language)}</output>
            <output data-testid="count">{formatNumber(12345, language.language)}</output>
            <div data-testid="ip-whitelist">
                <ApiKeyIpWhitelist apiKey={localeApiKey} />
            </div>
            <output data-testid="upload-errors">
                {t("errors.Failed to upload attachment. File size may be too large (Max size is {size}MB).", { size: 1234 })}
                {t("errors.{num} files could not be uploaded. File size may be too large (Max size is {size}MB).", { num: 2345, size: 1234 })}
            </output>
            <output data-testid="hidden-counts">
                {t("card.Flipped cards", { count: 1234 })}
                {t("card.Show all attachments ({attachments} hidden)", { attachments: 1234 })}
                {t("card.Show all checklists ({checklists} hidden)", { checklists: 1234 })}
            </output>
            <output data-testid="file-size">{formatFileSize(1536, language.language, 1)}</output>
            <div data-testid="board-comment-count" className="max-w-80">
                <select
                    aria-label="Fixture work state"
                    defaultValue="missing"
                    onChange={(event) => {
                        const scenario = event.target.value;
                        const card = ProjectCard.Model.getModel("locale-outline")!;
                        const reason = { code: "dependency_unavailable", message: "SECRET prerequisite title", source_ref: "card:secret-uid" };
                        const state: IWorkState = {
                            version: 1,
                            workflow_stage: "active",
                            completed: false,
                            verification_state: "unverified",
                            verification_source_change_seq: 0,
                            verification: null,
                            execution_state: null,
                            blocker_state: null,
                            material_kind: "work",
                            lifecycle: "active",
                            active_queue_eligible: null,
                            checklist_progress: { total: 2, completed: 2 },
                            reasons: [],
                            state_inconsistency: [],
                        };
                        if (scenario === "blocked") {
                            state.blocker_state = "blocked";
                            state.reasons = [reason];
                        }
                        if (scenario === "approval") {
                            state.blocker_state = "needs_approval";
                            state.reasons = [{ ...reason, code: "approval_pending" }];
                        }
                        if (scenario === "closed") {
                            state.completed = true;
                            state.checklist_progress.completed = 1;
                            state.workflow_stage = "closed";
                            state.state_inconsistency = [{ ...reason, code: "closed_with_open_checkitems" }];
                        }
                        if (scenario === "unknown") state.reasons = [{ ...reason, code: "future_reason" }];
                        card.work_state = scenario === "missing" ? undefined : state;
                    }}
                >
                    {["missing", "blocked", "approval", "closed", "active", "unknown"].map((scenario) => (
                        <option key={scenario}>{scenario}</option>
                    ))}
                </select>
                <BoardProvider project={fixtureProject} currentUser={documentUser}>
                    <ModelRegistry.ProjectCard.Provider model={ProjectCard.Model.getModel("locale-outline")!} params={{ setFilters: () => {} }}>
                        <BoardColumnCardCollapsible isDragging={false} />
                    </ModelRegistry.ProjectCard.Provider>
                </BoardProvider>
            </div>
            <div data-testid="card-work-state-detail" className="max-w-80">
                <CardWorkbenchLenses card={ProjectCard.Model.getModel("locale-outline")!} lens="execution" />
            </div>
            <div data-testid="document-progress">
                <BoardCardAttachmentDocumentProgress cardUID="locale-document" attachmentUID="locale-attachment" />
            </div>
            <div data-testid="image-progress">
                <ImageProgress file={uploadingImage} progress={12.4} />
            </div>
            <div data-testid="calendar">
                <Calendar value={new Date(2026, 9, 3, 13)} onChange={() => {}} hideTime />
            </div>
            <output data-testid="exact">{formatDateTime(date, language.language)}</output>
            {SUPPORTED_LOCALES.map((locale) => (
                <button key={locale} onClick={() => void language.changeLanguage(locale)}>
                    {locale}
                </button>
            ))}
        </>
    );
}
function render() {
    createRoot(document.getElementById("root")!).render(
        <QueryClientProvider client={new QueryClient()}>
            <MemoryRouter>
                <AuthProvider>
                    <Fixture />
                </AuthProvider>
            </MemoryRouter>
        </QueryClientProvider>
    );
}
if (i18n.isInitialized) render();
else i18n.on("initialized", render);
