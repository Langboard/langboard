import { cn } from "@/core/utils/ComponentUtils";
import ProjectItem, { SkeletonProjectItem } from "@/pages/DashboardPage/components/ProjectItem";
import Box from "@/components/base/Box";
import { Project } from "@/core/models";
import useInfiniteScrollPager from "@/core/hooks/useInfiniteScrollPager";
import { useTranslation } from "react-i18next";
import { memo } from "react";
import InfiniteScroller from "@/components/InfiniteScroller";

export function SkeletonProjectList({ ref }: { ref?: React.Ref<HTMLDivElement> }): React.JSX.Element {
    const skeletonCards = [];
    for (let i = 0; i < 4; ++i) {
        skeletonCards.push(<SkeletonProjectItem key={i} />);
    }

    return (
        <Box mt="4" ref={ref}>
            <Box display={{ initial: "hidden", md: "grid" }} gap="4" className="md:grid-cols-2 lg:grid-cols-4">
                {skeletonCards}
            </Box>
            <Box display={{ initial: "hidden", sm: "grid", md: "hidden" }} gap="4" className="sm:grid-cols-2 lg:grid-cols-4">
                {skeletonCards.slice(0, 2)}
            </Box>
            <Box display={{ initial: "grid", sm: "hidden" }} gap="4" className="sm:grid-cols-2 lg:grid-cols-4">
                {skeletonCards[0]}
            </Box>
        </Box>
    );
}

export interface IProjectListProps {
    projects: Project.TModel[];
    updateStarredProjects: React.DispatchWithoutAction;
    scrollAreaUpdater?: [number, React.DispatchWithoutAction];
    showRelatedBadge?: boolean;
    className?: string;
    rowClassName?: string;
}

const ProjectList = memo(
    ({
        projects,
        updateStarredProjects,
        scrollAreaUpdater,
        className,
        rowClassName,
        showRelatedBadge,
    }: IProjectListProps): React.JSX.Element | null => {
        const [t] = useTranslation();
        const PAGE_SIZE = 16;
        const { items, nextPage, hasMore } = useInfiniteScrollPager({ allItems: projects, size: PAGE_SIZE, updater: scrollAreaUpdater });

        return (
            <InfiniteScroller.Grid
                as={Box}
                row={Box}
                className={cn("mt-4", className)}
                rowClassName={rowClassName ?? "md:grid-cols-2 lg:grid-cols-4"}
                scrollable={() => document.getElementById("main")}
                loadMore={nextPage}
                hasMore={hasMore}
                totalCount={projects.length}
                loader={<SkeletonProjectList />}
            >
                {items.map((project) => (
                    <Box key={project.uid} data-project-uid={project.uid}>
                        <ProjectItem project={project} updateStarredProjects={updateStarredProjects} />
                        {showRelatedBadge && project.starred && project.related_to_current_user && (
                            <span className="px-2 text-xs text-muted-foreground">{t("dashboard.Related to me")}</span>
                        )}
                    </Box>
                ))}
            </InfiniteScroller.Grid>
        );
    }
);

export default ProjectList;
