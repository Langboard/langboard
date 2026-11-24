import { compareProjectActivityPriority, type IProjectActivityPriority as IActivityPriorityProject } from "./ProjectActivityPriority.ts";
import { defaultFilter } from "cmdk";

export const PROJECT_RECENT_WORK_LIMIT = 6;
export const PROJECT_RELATED_TO_ME_LIMIT = 6;
export const PROJECT_QUICK_SWITCHER_EVENT = "langboard:open-project-quick-switcher";

export interface ICommandPaletteCard {
    projectUID: string;
    cardUID: string;
    title: string;
}

export const buildCommandPaletteCards = (
    openCards: readonly ICommandPaletteCard[],
    boardCards: readonly ICommandPaletteCard[],
    authorizedProjectUIDs: ReadonlySet<string>
): ICommandPaletteCard[] => {
    const results = openCards.filter((card) => authorizedProjectUIDs.has(card.projectUID));
    const known = new Set(results.map((card) => `${card.projectUID}:${card.cardUID}`));
    for (const card of boardCards) {
        if (!authorizedProjectUIDs.has(card.projectUID)) continue;
        const key = `${card.projectUID}:${card.cardUID}`;
        if (known.has(key)) continue;
        results.push(card);
        known.add(key);
    }
    return results;
};

export type TProjectListView = "compact" | "cards";

export interface IProjectDiscoverySections<TProject extends IActivityPriorityProject> {
    all: TProject[];
    favorites: TProject[];
    related: TProject[];
    recent: TProject[];
}

export const buildProjectDiscoverySections = <TProject extends IActivityPriorityProject>(
    projects: readonly TProject[],
    recentLimit = PROJECT_RECENT_WORK_LIMIT,
    preserveOrder = false
): IProjectDiscoverySections<TProject> => {
    const unique = [...new Map(projects.map((project) => [project.uid, project])).values()];
    const all = preserveOrder ? unique : unique.sort(compareProjectActivityPriority);
    return {
        all,
        favorites: all.filter((project) => project.starred),
        related: all.filter((project) => !project.starred && project.related_to_current_user).slice(0, PROJECT_RELATED_TO_ME_LIMIT),
        recent: all.filter((project) => !project.starred && !project.related_to_current_user && project.last_activity_at).slice(0, recentLimit),
    };
};

export const buildProjectQuickSwitcherSections = <TProject extends IActivityPriorityProject>(projects: readonly TProject[]) => {
    const { all, favorites, related, recent } = buildProjectDiscoverySections(projects);
    const promotedUIDs = new Set([...favorites, ...related, ...recent].map((project) => project.uid));
    return {
        favorites,
        related,
        recent,
        other: all.filter((project) => !promotedUIDs.has(project.uid)),
    };
};

export const projectListViewStorageKey = (userUID: string) => `langboard:project-list-view:${userUID}`;

export const parseProjectListView = (value: string | null | undefined): TProjectListView => (value === "cards" ? "cards" : "compact");

export const isProjectQuickSwitcherShortcut = (event: Pick<KeyboardEvent, "altKey" | "ctrlKey" | "key" | "metaKey" | "shiftKey">) =>
    event.key.toLowerCase() === "k" && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey;

export const projectQuickSwitcherShortcutLabel = (platform: string): string => (/mac|iphone|ipad/i.test(platform) ? "⌘K" : "Ctrl K");

export const searchProjects = <TProject extends IActivityPriorityProject>(projects: readonly TProject[], query: string): TProject[] => {
    const search = query.trim();
    return [...new Map(projects.map((project) => [project.uid, project])).values()]
        .map((project) => ({ project, score: search ? defaultFilter(project.title, search) : 1 }))
        .filter(({ score }) => score > 0)
        .sort((a, b) => b.score - a.score || compareProjectActivityPriority(a.project, b.project))
        .map(({ project }) => project);
};

export type TProjectDiscoveryFilter = "unread" | "related" | "favorites" | "unfinished";
export const PROJECT_DISCOVERY_SORTS = ["default", "activity", "activity-oldest", "viewed", "name", "name-desc", "unread"] as const;
export type TProjectDiscoverySort = (typeof PROJECT_DISCOVERY_SORTS)[number];
export const projectDiscoverySortStorageKey = (userUID: string) => `langboard:project-list-sort:${userUID}`;
export const parseProjectDiscoverySort = (value: string | null): TProjectDiscoverySort =>
    PROJECT_DISCOVERY_SORTS.find((sort) => sort === value) ?? "default";
export interface IProjectDiscoveryMetadata extends IActivityPriorityProject {
    board_has_unread_change?: boolean;
    last_viewed_at?: Date | null;
}

export function selectDiscoveryProjects<T extends IProjectDiscoveryMetadata>(
    projects: readonly T[],
    query: string,
    filters: readonly TProjectDiscoveryFilter[],
    sort: TProjectDiscoverySort,
    unfinishedUIDs: ReadonlySet<string> = new Set(),
    locale?: string
): T[] {
    const matched = searchProjects(projects, query).filter((project) =>
        filters.every((filter) => {
            switch (filter) {
                case "unread":
                    return project.board_has_unread_change === true;
                case "related":
                    return project.related_to_current_user === true;
                case "favorites":
                    return project.starred;
                case "unfinished":
                    return unfinishedUIDs.has(project.uid);
            }
        })
    );
    const timestamp = (value: Date | null | undefined, missing = -Infinity) =>
        value && Number.isFinite(value.getTime()) ? value.getTime() : missing;
    if (sort === "default") return matched;
    const byName = sort === "name" || sort === "name-desc";
    const byActivity = sort === "activity" || sort === "activity-oldest";
    const collator = byName ? new Intl.Collator(locale, { sensitivity: "base", numeric: true }) : undefined;
    return matched.sort((a, b) => {
        if (byName || byActivity) {
            const favoriteOrder = Number(b.starred) - Number(a.starred);
            if (favoriteOrder) return favoriteOrder;
        }
        let order = 0;
        if (collator) order = collator.compare(a.title, b.title) * (sort === "name-desc" ? -1 : 1);
        if (sort === "activity") order = timestamp(b.last_activity_at) - timestamp(a.last_activity_at);
        if (sort === "activity-oldest") order = timestamp(a.last_activity_at, Infinity) - timestamp(b.last_activity_at, Infinity);
        if (sort === "viewed") order = timestamp(b.last_viewed_at) - timestamp(a.last_viewed_at);
        if (sort === "unread") order = Number(b.board_has_unread_change === true) - Number(a.board_has_unread_change === true);
        return (Number.isNaN(order) ? 0 : order) || a.uid.localeCompare(b.uid);
    });
}

export function buildProjectSearchSections<T extends IActivityPriorityProject>(projects: readonly T[]) {
    const unique = [...new Map(projects.map((project) => [project.uid, project])).values()];
    return {
        favorites: unique.filter((project) => project.starred),
        related: unique.filter((project) => !project.starred && project.related_to_current_user),
        other: unique.filter((project) => !project.starred && !project.related_to_current_user),
    };
}
