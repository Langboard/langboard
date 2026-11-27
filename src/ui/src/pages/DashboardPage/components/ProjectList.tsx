import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import ProjectItem, { SkeletonProjectItem } from "@/pages/DashboardPage/components/ProjectItem";
import Box from "@/components/base/Box";
import { Project } from "@/core/models";
import useInfiniteScrollPager from "@/core/hooks/useInfiniteScrollPager";
import { memo } from "react";
import InfiniteScroller from "@/components/InfiniteScroller";

export function SkeletonProjectList({ ref }: { ref?: React.Ref<HTMLDivElement> }): React.JSX.Element {
    const skeletonCards = [];
    for (let i = 0; i < 4; ++i) {
        skeletonCards.push(<SkeletonProjectItem key={Utils.String.Token.shortUUID()} />);
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
    scrollAreaUpdater: [number, React.DispatchWithoutAction];
    className?: string;
}

const ProjectList = memo(({ projects, updateStarredProjects, scrollAreaUpdater, className }: IProjectListProps): React.JSX.Element | null => {
    const PAGE_SIZE = 16;
    const { items, nextPage, hasMore } = useInfiniteScrollPager({ allItems: projects, size: PAGE_SIZE, updater: scrollAreaUpdater });

    return (
        <InfiniteScroller.Grid
            as={Box}
            row={Box}
            className={cn("mt-4", className)}
            rowClassName="md:grid-cols-2 lg:grid-cols-4"
            scrollable={() => document.getElementById("main")}
            loadMore={nextPage}
            hasMore={hasMore}
            totalCount={projects.length}
            loader={<SkeletonProjectList key={Utils.String.Token.shortUUID()} />}
        >
            {items.map((project) => (
                <ProjectItem key={project.uid} project={project} updateStarredProjects={updateStarredProjects} />
            ))}
        </InfiniteScroller.Grid>
    );
});

export default ProjectList;
