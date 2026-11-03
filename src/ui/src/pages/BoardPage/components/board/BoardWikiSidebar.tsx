import { useEffect, useRef } from "react";
import { draggable } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { useBoard } from "@/core/providers/BoardProvider";
import { WIKI_CARD_DRAG_TYPE, WIKI_CARD_DRAG_SOURCE_ATTR } from "./WikiCardDropData";
import { useTranslation } from "react-i18next";
import { usePageLocation } from "@/core/hooks/usePageLocation";
import Button from "@/components/base/Button";
import useGetWikiSummaries from "@/controllers/api/wiki/useGetWikiSummaries";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";

export default function BoardWikiSidebar({ projectUID, onNavigate }: { projectUID: string; onNavigate?: () => void }) {
    const [t] = useTranslation();
    const { canDragCards } = useBoard();
    const navigate = usePageNavigateRef();
    const location = usePageLocation();
    const { data, isFetching, error, refetch, fetchNextPage, hasNextPage, isFetchNextPageError } = useGetWikiSummaries(projectUID);
    const wikis = data?.pages.flatMap((page) => page.items) ?? [];
    const open = (url: string) => {
        navigate(url);
        onNavigate?.();
    };

    return (
        <nav aria-label={t("board.Wiki")} data-workbench-context="" className="flex h-full w-full min-w-0 flex-col overflow-hidden">
            <div className="flex shrink-0 items-center justify-between border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide">
                <span>{t("board.Wiki")}</span>
                <button type="button" className="text-primary hover:underline" onClick={() => open(ROUTES.BOARD.WIKI(projectUID))}>
                    {t("dashboard.Open wiki")}
                </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
                {error ? (
                    <div role="alert" className="flex flex-wrap items-center gap-2 px-2 py-3">
                        <span className="text-sm text-muted-foreground">{t("dashboard.Could not load wikis")}</span>
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={isFetching}
                            onClick={() => void (isFetchNextPageError ? fetchNextPage() : refetch())}
                        >
                            {t("dashboard.Retry")}
                        </Button>
                    </div>
                ) : null}
                {isFetching && !data ? (
                    <p role="status" className="px-2 py-3 text-sm text-muted-foreground">
                        {t("dashboard.Loading wikis")}
                    </p>
                ) : !data && error ? null : !wikis.length ? (
                    <p className="px-2 py-3 text-sm text-muted-foreground">{t("dashboard.No wikis")}</p>
                ) : (
                    wikis.map((wiki) => {
                        const route = ROUTES.BOARD.WIKI_PAGE(projectUID, wiki.wiki_uid);
                        return (
                            <WikiSidebarItem
                                key={wiki.wiki_uid}
                                projectUID={projectUID}
                                wikiUID={wiki.wiki_uid}
                                canLink={canDragCards && wiki.is_public === true}
                                type="button"
                                aria-current={location.pathname === route ? "page" : undefined}
                                className={cn(
                                    "block w-full truncate rounded px-2 py-1 text-left text-sm hover:bg-muted",
                                    "aria-[current=page]:bg-muted aria-[current=page]:text-primary"
                                )}
                                title={wiki.title}
                                onClick={() => open(route)}
                            >
                                {wiki.title}
                            </WikiSidebarItem>
                        );
                    })
                )}
                {data && hasNextPage && !error ? (
                    <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="btn btn-sm btn-outline mt-2 w-full"
                        disabled={isFetching}
                        onClick={() => void fetchNextPage()}
                    >
                        {t("common.More")}
                    </Button>
                ) : null}
            </div>
        </nav>
    );
}

function WikiSidebarItem({
    projectUID,
    wikiUID,
    canLink,
    ...props
}: React.ComponentProps<"button"> & {
    projectUID: string;
    wikiUID: string;
    canLink: boolean;
}) {
    const ref = useRef<HTMLButtonElement>(null);
    const canLinkRef = useRef(canLink);
    canLinkRef.current = canLink;
    useEffect(() => {
        if (!ref.current || !canLink) return;
        return draggable({
            element: ref.current,
            canDrag: () => canLinkRef.current,
            getInitialData: () => ({ type: WIKI_CARD_DRAG_TYPE, projectUID, wikiUID }),
        });
    }, [projectUID, wikiUID, canLink]);
    return <button {...props} ref={ref} {...{ [WIKI_CARD_DRAG_SOURCE_ATTR]: canLink ? wikiUID : undefined }} />;
}
