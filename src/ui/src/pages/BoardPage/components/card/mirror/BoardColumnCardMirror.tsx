import Card from "@/components/base/Card";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardController } from "@/core/providers/BoardController";
import { useBoard } from "@/core/providers/BoardProvider";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { captureCardOrigin } from "../../board/CardAnimation";
import { useTranslation } from "react-i18next";

export default function BoardColumnCardMirror({ isDragging, compact }: { isDragging: boolean; compact?: boolean }) {
    const { model: card } = ModelRegistry.ProjectCard.useContext();
    const resource = card.useField("linked_resource");
    const { selectCardViewType } = useBoardController();
    const { project, navigateWithFilters } = useBoard();
    const [t] = useTranslation();
    const available = resource?.status === "available";
    const title = available ? resource.title : t("wiki.Source unavailable");
    const open = (element: HTMLDivElement) => {
        if (isDragging || selectCardViewType) return;
        captureCardOrigin(project.uid, card.uid, element.getBoundingClientRect());
        navigateWithFilters(ROUTES.BOARD.CARD(project.uid, card.uid));
    };
    return (
        <Card.Root
            id={`board-card-${card.uid}`}
            data-card-mirror=""
            data-reference-status={resource?.status ?? "unknown"}
            role="button"
            tabIndex={selectCardViewType ? -1 : 0}
            aria-disabled={!!selectCardViewType}
            className={cn(
                "group/card relative cursor-pointer hover:border-primary",
                compact && "border-border/60 bg-background/80 shadow-none",
                !!selectCardViewType && "cursor-not-allowed opacity-50"
            )}
            onClick={(event) => open(event.currentTarget)}
            onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    open(event.currentTarget);
                }
            }}
        >
            <Card.Header className={cn("relative block space-y-0", compact ? "px-3 py-2" : "py-4")}>
                <p className="mb-2 text-xs font-medium text-muted-foreground">{t("wiki.Linked card")}</p>
                <Card.Title className="break-words text-[0.95rem] leading-snug">{title}</Card.Title>
                <p className="mt-2 text-xs text-muted-foreground">{t("wiki.Reference only; not active work")}</p>
                {!available && <p className="mt-1 text-xs text-muted-foreground">{t("wiki.Source unavailable explanation")}</p>}
            </Card.Header>
        </Card.Root>
    );
}
