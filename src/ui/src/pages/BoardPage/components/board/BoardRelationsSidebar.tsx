import Button from "@/components/base/Button";
import CardCompactLabels from "@/components/LabelBadge/CardCompactLabels";
import useGetCardRelationSummaries, { ICardRelationSummary, relationAuthScope } from "@/controllers/api/card/useGetCardRelationSummaries";
import { formatNumber } from "@/core/utils/LocaleFormat";
import { useTranslation } from "react-i18next";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import useAuthStore from "@/core/stores/AuthStore";
import { ProjectCard } from "@/core/models";
import { RELATIONSHIP_GROUPS } from "@/pages/BoardPage/components/board/RelationshipTypePicker";
import { ChevronDown, ChevronRight, Link2 } from "lucide-react";
import { useState } from "react";

const MAX_TREE_DEPTH = 8;

export default function BoardRelationsSidebar({ projectUID, cardUID }: { projectUID: string; cardUID?: string }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const authScope = useAuthStore(relationAuthScope);
    const actorUID = useAuthStore((state) => state.currentUser?.uid);

    return (
        <nav aria-label={t("dashboard.Relations")} data-workbench-context="" className="flex h-full w-full min-w-0 flex-col overflow-hidden">
            <div className="flex shrink-0 items-center justify-between border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide">
                <span>{t("dashboard.Relations")}</span>
                <button type="button" className="text-primary hover:underline" onClick={() => navigate(ROUTES.BOARD.GRAPH(projectUID))}>
                    {t("dashboard.Full graph")}
                </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
                {cardUID && actorUID ? (
                    <RelationsTree key={`${authScope}:${projectUID}:${cardUID}`} projectUID={projectUID} cardUID={cardUID} ancestors={[]} />
                ) : (
                    <p className="px-2 py-3 text-sm text-muted-foreground">{t("dashboard.Open a card to see its context")}</p>
                )}
            </div>
        </nav>
    );
}

function RelationsTree({ projectUID, cardUID, ancestors }: { projectUID: string; cardUID: string; ancestors: string[] }) {
    const [t, i18n] = useTranslation();
    const query = useGetCardRelationSummaries(projectUID, cardUID);
    const items = query.data?.pages.flatMap((page) => page.items) ?? [];
    const card = query.data?.pages[0].card;
    const cachedCard = ProjectCard.Model.useModel(cardUID, [cardUID]);
    const sections = [
        ...RELATIONSHIP_GROUPS.map((group) => ({
            key: group.semantic,
            label: t(group.label),
            Icon: group.Icon,
            items: items.filter(({ relationship }) => relationship.machine_semantic === group.semantic),
        })),
        {
            key: "legacy",
            label: t("board.Legacy unclassified relationships"),
            Icon: Link2,
            items: items.filter(({ relationship }) => !RELATIONSHIP_GROUPS.some((group) => group.semantic === relationship.machine_semantic)),
        },
    ].filter((group) => group.items.length);

    return (
        <div className="space-y-2 text-sm" aria-busy={query.isFetching}>
            {!ancestors.length && card && (
                <p className="truncate px-2 font-medium" title={card.title}>
                    {card.title}
                </p>
            )}
            {!ancestors.length && card && cachedCard && <CachedBlockers card={cachedCard} items={items} />}
            {query.isError ? (
                <div role="alert" className="px-2 py-1 text-xs text-muted-foreground">
                    <p>{t("dashboard.Could not load relationships", { defaultValue: "Could not load relationships" })}</p>
                    <Button
                        size="sm"
                        variant="ghost"
                        disabled={query.isFetching}
                        onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}
                    >
                        {t("dashboard.Retry")}
                    </Button>
                </div>
            ) : null}
            {sections.map(({ key, label, Icon, items: sectionItems }) => (
                <section key={key} aria-label={label}>
                    <h2 className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-muted-foreground">
                        <Icon size={14} aria-hidden="true" /> {label} · {formatNumber(sectionItems.length, i18n.language)}
                    </h2>
                    <ul className="space-y-1">
                        {sectionItems.map((item) => (
                            <RelationBranch
                                key={item.relationship.uid}
                                item={item}
                                projectUID={projectUID}
                                cardUID={cardUID}
                                ancestors={[...ancestors, cardUID]}
                            />
                        ))}
                    </ul>
                </section>
            ))}
            {query.isPending || query.isFetchingNextPage ? (
                <p role="status" className="px-2 py-1 text-xs text-muted-foreground">
                    {t("dashboard.Loading relationships", { defaultValue: "Loading relationships" })}
                </p>
            ) : !query.isError && !items.length ? (
                <p className="px-2 text-muted-foreground">{t("dashboard.No relationships")}</p>
            ) : null}
            {query.hasNextPage && !query.isError && (
                <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => void query.fetchNextPage()}>
                    {t("dashboard.More relationships", { defaultValue: "More relationships" })}
                </Button>
            )}
        </div>
    );
}

function CachedBlockers({ card, items }: { card: ProjectCard.TModel; items: ICardRelationSummary[] }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const workState = card.useField("work_state");
    const blockers = workState?.dependency_state?.direct_blockers ?? [];
    // Cached readiness can annotate only endpoints reauthorized by this summary page.
    const visible = items.filter((item) => blockers.some((blocker) => blocker.relationship_uid === item.relationship.uid && blocker.accessible));
    const inaccessibleCount = blockers.filter((blocker) => blocker.accessible === false).length;
    if (!visible.length && !inaccessibleCount) return null;
    return (
        <section aria-label={t("board.Blockers")}>
            <h2 className="px-2 py-1 text-xs font-medium text-muted-foreground">
                {t("board.Blockers · {{count}}", { count: visible.length + inaccessibleCount })}
            </h2>
            {visible.map(({ relationship, target }) => (
                <button
                    key={relationship.uid}
                    type="button"
                    className="block w-full truncate rounded px-2 py-1 text-left hover:bg-muted"
                    onClick={() => navigate(ROUTES.BOARD.CARD(target.project_uid, target.uid))}
                >
                    {target.title}
                </button>
            ))}
            {Array.from({ length: inaccessibleCount }, (_, index) => (
                <p key={index} className="px-2 py-1 text-muted-foreground">
                    {t("board.Inaccessible prerequisite")}
                </p>
            ))}
        </section>
    );
}

function RelationBranch({
    item,
    projectUID,
    cardUID,
    ancestors,
}: {
    item: ICardRelationSummary;
    projectUID: string;
    cardUID: string;
    ancestors: string[];
}) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const [expanded, setExpanded] = useState(false);
    const { relationship, target } = item;
    const cachedTarget = ProjectCard.Model.useModel(target.uid, [target.uid]);
    const cycle = ancestors.includes(target.uid);
    const depthLimited = ancestors.length >= MAX_TREE_DEPTH;
    const relationName = relationship.child_card_uid === cardUID ? relationship.parent_name : relationship.child_name;

    return (
        <li>
            <div className="flex min-w-0 items-start gap-1">
                <Button
                    type="button"
                    size="icon-sm"
                    variant="ghost"
                    className="shrink-0"
                    aria-expanded={cycle || depthLimited ? undefined : expanded}
                    aria-label={
                        expanded
                            ? t("dashboard.Collapse relationships for {{title}}", {
                                  title: target.title,
                                  defaultValue: "Collapse relationships for {{title}}",
                              })
                            : t("dashboard.Expand relationships for {{title}}", {
                                  title: target.title,
                                  defaultValue: "Expand relationships for {{title}}",
                              })
                    }
                    disabled={cycle || depthLimited}
                    onClick={() => setExpanded(!expanded)}
                >
                    {expanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                </Button>
                <button
                    type="button"
                    className="flex min-w-0 flex-1 flex-col rounded px-1 py-0.5 text-left hover:bg-muted"
                    onClick={() => navigate(ROUTES.BOARD.CARD(target.project_uid, target.uid))}
                    title={target.title}
                >
                    <span className="w-full truncate text-xs leading-4">{target.title}</span>
                    <span className="w-full truncate text-[10px] leading-3 text-muted-foreground">
                        {relationName}
                        {target.project_uid !== projectUID && target.project_title ? ` · ${target.project_title}` : ""}
                    </span>
                </button>
                {cachedTarget?.project_uid === target.project_uid && <CardCompactLabels card={cachedTarget} />}
            </div>
            {cycle || depthLimited ? (
                <p className="pl-9 text-xs text-muted-foreground">
                    {cycle
                        ? t("dashboard.Already in this branch", { defaultValue: "Already in this branch" })
                        : t("dashboard.Open this card to continue", { defaultValue: "Open this card to continue" })}
                </p>
            ) : expanded ? (
                <div className="ml-3 border-l pl-1">
                    <RelationsTree projectUID={target.project_uid} cardUID={target.uid} ancestors={ancestors} />
                </div>
            ) : null}
        </li>
    );
}
