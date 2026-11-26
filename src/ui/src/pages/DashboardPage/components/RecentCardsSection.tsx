import { useEffect } from "react";
import { useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/base/IconComponent";
import { type Project, ProjectCard } from "@/core/models";
import Tooltip from "@/components/base/Tooltip";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import { closeCard, toggleCardPin } from "./OpenCardsStore";
import useValidateRecentCards from "./useValidateRecentCards";
import type { IOpenCard } from "./OpenCardsData";
import { projectIdentityColor } from "./projectIdentityColor";
import ExplorerSection, { EXPLORER_SECTION_LIMIT, IExplorerSectionProps } from "./ExplorerSection";

export default function RecentCardsSection({
    userUID,
    cards: visibleOpenCards,
    projectsByUID,
    collapsed: openCardsCollapsed,
    expanded: showOlderCards,
    onToggle,
    onExpand,
    onNavigate,
    active = true,
    headerProps,
    reorder,
}: {
    userUID: string;
    cards: IOpenCard[];
    projectsByUID?: ReadonlyMap<string, Project.TModel>;
    collapsed: boolean;
    expanded: boolean;
    onToggle: () => void;
    onExpand: () => void;
    onNavigate?: () => void;
    active?: boolean;
    headerProps?: IExplorerSectionProps["headerProps"];
    reorder?: IExplorerSectionProps["reorder"];
}) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const location = useLocation();
    const displayedCards = showOlderCards ? visibleOpenCards : visibleOpenCards.slice(0, EXPLORER_SECTION_LIMIT);
    useValidateRecentCards(userUID, displayedCards, active && !openCardsCollapsed);
    const projectUIDs = [...new Set(visibleOpenCards.map((card) => card.projectUID))].sort().join(",");
    useEffect(() => {
        const socket = useSocketOutsideProvider();
        // useGetProjects owns these Dashboard subscriptions. Listen without a second owner.
        const listeners = projectUIDs
            .split(",")
            .filter(Boolean)
            .map((uid) => ({
                topic: ESocketTopic.Dashboard as const,
                topicId: uid,
                event: SocketEvents.SERVER.DASHBOARD.CARD.DELETED.replace("{uid}", uid),
                eventKey: `recent-card-deleted-${userUID}-${uid}`,
                callback: (data: unknown) => {
                    if (data && typeof data === "object" && "uid" in data && typeof data.uid === "string") closeCard(userUID, uid, data.uid);
                },
            }));
        listeners.forEach((props) => socket.on(props));
        return () => listeners.forEach((props) => socket.off(props));
    }, [userUID, projectUIDs]);
    return (
        <ExplorerSection
            title={t("dashboard.Recent cards")}
            icon="history"
            className="border-b border-border pb-2"
            count={visibleOpenCards.length}
            collapsed={openCardsCollapsed}
            expanded={showOlderCards}
            onToggle={onToggle}
            onExpand={onExpand}
            headerProps={headerProps}
            reorder={reorder}
        >
            <div data-recent-card-list="">
                {displayedCards.map((card) => {
                    const active = location.pathname === ROUTES.BOARD.CARD(card.projectUID, card.cardUID);
                    const project = projectsByUID?.get(card.projectUID);
                    return (
                        <div key={`${card.projectUID}:${card.cardUID}`} className="group flex min-h-8 items-center rounded-md hover:bg-muted">
                            <Tooltip.Root>
                                <Tooltip.Trigger asChild>
                                    <button
                                        type="button"
                                        aria-current={active ? "page" : undefined}
                                        title={project ? undefined : card.title}
                                        className={cn(
                                            "flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left text-sm",
                                            active && "bg-muted text-primary"
                                        )}
                                        onClick={() => {
                                            navigate(ROUTES.BOARD.CARD(card.projectUID, card.cardUID));
                                            onNavigate?.();
                                        }}
                                    >
                                        <span
                                            aria-hidden="true"
                                            data-recent-card-project-marker=""
                                            className="h-3 w-1.5 shrink-0 rounded-full"
                                            style={{ backgroundColor: projectIdentityColor(card.projectUID) }}
                                        />
                                        <span className="truncate">{card.title}</span>
                                        <OpenCardUnreadDot cardUID={card.cardUID} />
                                    </button>
                                </Tooltip.Trigger>
                                {project && <RecentCardTooltip card={card} project={project} />}
                            </Tooltip.Root>
                            <button
                                type="button"
                                aria-label={t(card.pinned ? "dashboard.Unpin card" : "dashboard.Pin card")}
                                title={t(card.pinned ? "dashboard.Unpin card" : "dashboard.Pin card")}
                                className={cn("rounded p-1 hover:bg-accent", !card.pinned && "opacity-0 focus:opacity-100 group-hover:opacity-100")}
                                onClick={() => toggleCardPin(userUID, card.projectUID, card.cardUID)}
                            >
                                <IconComponent icon="pin" size="3" className={card.pinned ? "text-primary" : undefined} />
                            </button>
                            <button
                                type="button"
                                aria-label={t("dashboard.Close card from list")}
                                title={t("dashboard.Close card from list")}
                                className="mr-1 rounded p-1 opacity-0 hover:bg-accent focus:opacity-100 group-hover:opacity-100"
                                onClick={() => closeCard(userUID, card.projectUID, card.cardUID)}
                            >
                                <IconComponent icon="x" size="3" />
                            </button>
                        </div>
                    );
                })}
            </div>
        </ExplorerSection>
    );
}

function RecentCardTooltip({ card, project }: { card: IOpenCard; project: Project.TModel }) {
    const projectTitle = project.useField("title");
    return (
        <Tooltip.Portal>
            <Tooltip.Content side="right" className="max-w-64 break-words">
                <span className="block font-medium">{card.title}</span>
                <span className="block text-xs text-muted-foreground">{projectTitle}</span>
                <code className="block break-all text-xs">{card.cardUID}</code>
            </Tooltip.Content>
        </Tooltip.Portal>
    );
}

function OpenCardUnreadDot({ cardUID }: { cardUID: string }) {
    const model = ProjectCard.Model.useModel(cardUID, [cardUID]);
    return model ? <LiveUnreadDot model={model} /> : null;
}

function LiveUnreadDot({ model }: { model: ProjectCard.TModel }) {
    const [t] = useTranslation();
    const unread = model.useField("has_unread_change");
    return unread ? <span aria-label={t("board.Unread changes")} className="ml-auto size-1.5 shrink-0 rounded-full bg-primary" /> : null;
}
