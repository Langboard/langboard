import { Project, ProjectCard, ProjectColumn } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useSocket } from "@/core/providers/SocketProvider";
import { useQueryClient } from "@tanstack/react-query";
import SignalInboxPanel from "./SignalInboxPanel";
import SignalOutboxPanel from "./SignalOutboxPanel";
import Tabs from "@/components/base/Tabs";
import useAuthStore from "@/core/stores/AuthStore";
import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

export default function BoardSignalInbox({ project }: { project: Project.TModel }) {
    const projectUID = project.uid;
    const actorScope = useAuthStore((state) => `${state.currentUser?.uid}:${state.getIdentityVersion()}:${state.getSessionVersion()}`);
    const [t] = useTranslation();
    const [tab, setTab] = useState("inbox");
    const tabsID = useId();
    const moveTab = (event: KeyboardEvent<HTMLButtonElement>) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? "inbox" : event.key === "End" ? "outbox" : tab === "inbox" ? "outbox" : "inbox";
        setTab(next);
        event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`[data-signal-tab="${next}"]`)?.focus();
    };
    const [visible, setVisible] = useState(document.visibilityState === "visible");
    useEffect(() => {
        setTab("inbox");
        const changed = () => {
            setVisible(document.visibilityState === "visible");
        };
        document.addEventListener("visibilitychange", changed);
        return () => document.removeEventListener("visibilitychange", changed);
    }, [projectUID, actorScope]);
    const socket = useSocket();
    const roleActions = project.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(roleActions);
    const columns = ProjectColumn.Model.useModels((column) => column.project_uid === projectUID, [projectUID]);
    const client = useQueryClient();
    const cards = ProjectCard.Model.useModels(
        (card) => card.project_uid === projectUID && !card.archived_at && card.source_type !== "project_wiki" && card.source_type !== "project_card",
        [projectUID]
    );
    return (
        <div className="flex h-full min-h-0 flex-col">
            <Tabs.Provider key={`${projectUID}:${actorScope}`} value={tab} onValueChange={setTab}>
                <Tabs.List size="sm" className="shrink-0" role="tablist" aria-label={`${t("card.inbox.title")} / ${t("card.outbox.title")}`}>
                    <Tabs.Trigger
                        value="inbox"
                        role="tab"
                        id={`${tabsID}-inbox`}
                        aria-controls={`${tabsID}-inbox-panel`}
                        aria-selected={tab === "inbox"}
                        tabIndex={tab === "inbox" ? 0 : -1}
                        data-signal-tab="inbox"
                        onKeyDown={moveTab}
                    >
                        {t("card.inbox.title")}
                    </Tabs.Trigger>
                    <Tabs.Trigger
                        value="outbox"
                        role="tab"
                        id={`${tabsID}-outbox`}
                        aria-controls={`${tabsID}-outbox-panel`}
                        aria-selected={tab === "outbox"}
                        tabIndex={tab === "outbox" ? 0 : -1}
                        data-signal-tab="outbox"
                        onKeyDown={moveTab}
                    >
                        {t("card.outbox.title")}
                    </Tabs.Trigger>
                </Tabs.List>
                {visible && (
                    <Tabs.Content
                        value="inbox"
                        role="tabpanel"
                        id={`${tabsID}-inbox-panel`}
                        aria-labelledby={`${tabsID}-inbox`}
                        className="min-h-0 flex-1"
                    >
                        <SignalInboxPanel
                            projectUID={projectUID}
                            socket={socket}
                            cards={cards}
                            columns={columns.filter((column) => !column.is_archive)}
                            onCreated={(cardUID) => {
                                void client.invalidateQueries({ queryKey: [`get-cards-${projectUID}`] });
                                void client.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${cardUID}`] });
                            }}
                            canEdit={hasRoleAction(ProjectRole.EAction.CardUpdate)}
                            onLinked={(cardUID) => {
                                void client.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${cardUID}`] });
                            }}
                        />
                    </Tabs.Content>
                )}
                {visible && (
                    <Tabs.Content
                        value="outbox"
                        role="tabpanel"
                        id={`${tabsID}-outbox-panel`}
                        aria-labelledby={`${tabsID}-outbox`}
                        className="min-h-0 flex-1"
                    >
                        <SignalOutboxPanel key={`${projectUID}:${actorScope}`} projectUID={projectUID} onPermissionDenied={() => setTab("inbox")} />
                    </Tabs.Content>
                )}
            </Tabs.Provider>
        </div>
    );
}
