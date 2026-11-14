import { Project, ProjectCard, ProjectColumn } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useSocket } from "@/core/providers/SocketProvider";
import { useQueryClient } from "@tanstack/react-query";
import SignalInboxPanel from "./SignalInboxPanel";

export default function BoardSignalInbox({ project }: { project: Project.TModel }) {
    const projectUID = project.uid;
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
    );
}
