import ProjectUnreadIndicator from "./ProjectUnreadIndicator";
import { BOARD_REFERENCE_DRAG_TYPE } from "./BoardReferenceDrag";
import ProjectAvatar from "./ProjectAvatar";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/core/utils/ComponentUtils";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import { Project } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { formatDateDistance } from "@/core/utils/LocaleFormat";
import ProjectItemStarButton from "@/pages/DashboardPage/components/ProjectItemStarButton";
import ContextMenu from "@/components/base/ContextMenu";
import { projectTypeLabel } from "@/pages/DashboardPage/components/ProjectTypeCopy";

import ProjectWorkloadBadges from "./ProjectWorkloadBadges";

interface IProjectCompactItemProps {
    project: Project.TModel;
    dense?: boolean;
    showRelatedBadge?: boolean;
    updateStarredProjects: React.DispatchWithoutAction;
}

const ProjectCompactItem = memo(({ project, updateStarredProjects, dense, showRelatedBadge }: IProjectCompactItemProps): React.JSX.Element => {
    const [t, i18n] = useTranslation();
    const navigate = usePageNavigateRef();
    const [isUpdating, setIsUpdating] = useState(false);
    const title = project.useField("title");
    const projectType = project.useField("project_type");
    const activityAt = project.last_activity_at ?? project.created_at;

    return (
        <ModelRegistry.Project.Provider model={project}>
            <ContextMenu.Root>
                <ContextMenu.Trigger asChild>
                    <Flex
                        data-project-uid={project.uid}
                        items="center"
                        style={{ containerType: "inline-size", containerName: "project-workload" }}
                        className="group min-w-0 rounded-xl border border-transparent pr-2 hover:border-border hover:bg-accent/70"
                    >
                        <Button
                            draggable
                            onDragStart={(event) => {
                                event.stopPropagation();
                                event.dataTransfer.setData(BOARD_REFERENCE_DRAG_TYPE, project.uid);
                                event.dataTransfer.effectAllowed = "copy";
                            }}
                            variant="ghost"
                            className={cn(
                                "flex h-auto min-w-0 flex-1 justify-start gap-3 rounded-xl px-3 text-left hover:bg-transparent",
                                dense ? "py-1.5" : "py-2.5"
                            )}
                            onClick={() => navigate(ROUTES.BOARD.MAIN(project.uid))}
                        >
                            <Flex
                                items="center"
                                justify="center"
                                className={cn("relative shrink-0 rounded-lg bg-secondary text-secondary-foreground", dense ? "size-7" : "size-9")}
                            >
                                <ProjectAvatar project={project} className="size-full rounded-lg object-cover" />
                                <ProjectUnreadIndicator project={project} className="-right-1 -top-1" />
                            </Flex>
                            <Box className="min-w-0 flex-1">
                                <Box className="truncate text-sm font-semibold" title={title}>
                                    {title}
                                </Box>
                                {showRelatedBadge && project.starred && project.related_to_current_user && (
                                    <span className="text-xs text-muted-foreground">{t("dashboard.Related to me")}</span>
                                )}
                                <Flex items="center" gap="1.5" className="mt-0.5 min-w-0 text-xs text-muted-foreground">
                                    <span className="truncate">{projectTypeLabel(t, projectType)}</span>
                                    <span aria-hidden="true">·</span>
                                    <span className="min-w-0 truncate">{formatDateDistance(activityAt, i18n.language)}</span>
                                </Flex>
                            </Box>
                        </Button>
                        <ProjectWorkloadBadges projectUID={project.uid} compact />
                        <ProjectItemStarButton
                            compact
                            isUpdating={isUpdating}
                            setIsUpdating={setIsUpdating}
                            updateStarredProjects={updateStarredProjects}
                        />
                    </Flex>
                </ContextMenu.Trigger>
                <ContextMenu.Content>
                    <ContextMenu.Item onSelect={() => navigate(ROUTES.BOARD.MAIN(project.uid))}>{title}</ContextMenu.Item>
                    <ProjectItemStarButton
                        menuItem
                        isUpdating={isUpdating}
                        setIsUpdating={setIsUpdating}
                        updateStarredProjects={updateStarredProjects}
                    />
                </ContextMenu.Content>
            </ContextMenu.Root>
        </ModelRegistry.Project.Provider>
    );
});
ProjectCompactItem.displayName = "Dashboard.ProjectCompactItem";

export default ProjectCompactItem;
