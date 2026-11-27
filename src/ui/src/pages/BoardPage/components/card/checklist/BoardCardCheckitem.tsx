import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Tooltip from "@/components/base/Tooltip";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ProjectCheckitem } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import BoardCardCheckitemAssignedMember from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemAssignedMember";
import BoardCardCheckitemCheckbox from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemCheckbox";
import BoardCardCheckitemMoreMenu from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemMoreMenu";
import BoardCardCheckitemTimer from "@/pages/BoardPage/components/card/checklist/BoardCardCheckitemTimer";
import { DropIndicator } from "@atlaskit/pragmatic-drag-and-drop-react-drop-indicator/box";
import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import invariant from "tiny-invariant";
import { columnRowDndHelpers } from "@/core/helpers/dnd";
import { BOARD_CARD_CHECK_DND_SYMBOL_SET } from "@/pages/BoardPage/components/card/checklist/BoardCardCheckConstants";
import { TRowState } from "@/core/helpers/dnd/types";
import { ROW_IDLE } from "@/core/helpers/dnd/createDndRowEvents";
import { Utils } from "@langboard/core/utils";
import { ProjectRole } from "@/core/models/roles";

export interface IBoardCardCheckitemProps {
    checkitem: ProjectCheckitem.TModel;
}

function BoardCardCheckitem({ checkitem }: IBoardCardCheckitemProps): React.JSX.Element {
    const { hasRoleAction } = useBoardCard();
    const outerRef = useRef<HTMLDivElement | null>(null);
    const draggableRef = useRef<HTMLButtonElement | null>(null);
    const [state, setState] = useState<TRowState>(ROW_IDLE);
    const canReorder = hasRoleAction(ProjectRole.EAction.CardUpdate);
    const order = checkitem.useField("order");
    const checklistUID = checkitem.useField("checklist_uid");

    useEffect(() => {
        if (!canReorder) {
            return;
        }

        const outer = outerRef.current;
        const draggable = draggableRef.current;
        invariant(outer && draggable);

        return columnRowDndHelpers.row({
            row: checkitem,
            symbolSet: BOARD_CARD_CHECK_DND_SYMBOL_SET,
            draggable: draggable,
            dropTarget: outer,
            setState,
            renderPreview({ container }) {
                // Simple drag preview generation: just cloning the current element.
                // Not using react for this.
                const rect = outer.getBoundingClientRect();
                const preview = outer.cloneNode(true);
                invariant(Utils.Type.isElement(preview, "div"));
                preview.style.width = `${rect.width}px`;
                preview.style.height = `${rect.height}px`;

                container.appendChild(preview);
            },
        });
    }, [checkitem, order, checklistUID]);

    const checkitemClassName = cn(
        "ml-3 relative border-accent border-b-2 border-transparent",
        "after:content-[''] after:absolute after:-top-[calc(50%_+_2px)] after:left-0",
        "after:border-l after:border-b after:border-accent after:h-[calc(100%_+_2px)] after:w-3"
    );

    return (
        <Box className={checkitemClassName} ref={outerRef}>
            {state.type === "is-over" && <DropIndicator edge={state.closestEdge} gap="2px" />}
            <BoardCardCheckitemDisplay checkitem={checkitem} canReorder={canReorder} draggableRef={draggableRef} />
        </Box>
    );
}

interface IBoardCardSubCheckitemDisplayProps {
    checkitem: ProjectCheckitem.TModel;
    canReorder: bool;
    draggableRef: React.RefObject<HTMLButtonElement | null>;
}

const BoardCardCheckitemDisplay = memo(({ checkitem, canReorder, draggableRef }: IBoardCardSubCheckitemDisplayProps) => {
    const { projectUID, card, currentUser, hasRoleAction, sharedClassNames } = useBoardCard();
    const navigate = usePageNavigateRef();
    const [t] = useTranslation();
    const [isValidating, setIsValidating] = useState(false);
    const [isTitleOpened, setIsTitleOpened] = useState(false);
    const title = checkitem.useField("title");
    const isChecked = checkitem.useField("is_checked");
    const deadline = checkitem.useField("deadline_at");
    const cardifiedCard = checkitem.useForeignFieldOne("cardified_card");
    const assignedUser = checkitem.useForeignFieldOne("user");
    const canEditCheckitem = (!assignedUser && hasRoleAction(ProjectRole.EAction.CardUpdate)) || assignedUser.uid === currentUser.uid;

    const toCardifiedCard = () => {
        if (!cardifiedCard) {
            return;
        }

        navigate(ROUTES.BOARD.CARD(projectUID, cardifiedCard.uid));
    };

    return (
        <ModelRegistry.ProjectCheckitem.Provider model={checkitem} params={{ canEdit: canEditCheckitem, isValidating, setIsValidating }}>
            <Flex
                items="center"
                justify="between"
                gap="2"
                h={{
                    initial: "16",
                    md: "12",
                }}
                className="ml-2 w-[calc(100%_-_theme(spacing.2))] truncate"
            >
                <Flex items="center" gap="2" w="full" className="truncate">
                    <Flex items="center" gap="1">
                        {canReorder && (
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                className="h-8 w-5 sm:size-8"
                                title={t("common.Drag to reorder")}
                                disabled={isValidating}
                                ref={draggableRef}
                            >
                                <IconComponent icon="grip-vertical" size="4" />
                            </Button>
                        )}
                    </Flex>
                    <Flex
                        direction={{
                            initial: "col",
                            md: "row",
                        }}
                        items={{
                            md: "center",
                        }}
                        gap={{ initial: "0.5", md: "2" }}
                        w="full"
                        className="truncate"
                    >
                        <Flex items="center" justify="between">
                            <Flex items="center" gap="2">
                                <BoardCardCheckitemCheckbox key={`board-card-checkitem-checkbox-${checkitem.uid}`} />
                                {cardifiedCard && (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        title={t("card.Go to cardified card")}
                                        size="icon-sm"
                                        className="h-8 w-5 sm:size-8"
                                        onClick={toCardifiedCard}
                                    >
                                        <IconComponent icon="square-chart-gantt" size="5" />
                                    </Button>
                                )}
                                {assignedUser && (
                                    <BoardCardCheckitemAssignedMember
                                        key={`board-card-checkitem-assigned-member-${checkitem.uid}`}
                                        projectUID={projectUID}
                                        cardUID={card.uid}
                                        assignedUser={assignedUser}
                                    />
                                )}
                            </Flex>
                            <Box display={{ initial: "block", md: "hidden" }}>
                                <BoardCardCheckitemTimer key={`board-card-checkitem-timer-${checkitem.uid}`} />
                            </Box>
                        </Flex>
                        <Flex items="center" gap="2" className="min-w-0">
                            <Tooltip.Root open={isTitleOpened} onOpenChange={setIsTitleOpened}>
                                <Tooltip.Trigger asChild onClick={() => setIsTitleOpened(!isTitleOpened)}>
                                    <span
                                        className={cn("truncate", cardifiedCard && "sm:pl-2", isChecked && "text-muted-foreground/70 line-through")}
                                    >
                                        {title}
                                    </span>
                                </Tooltip.Trigger>
                                <Tooltip.Content className={sharedClassNames.popoverContent}>{title}</Tooltip.Content>
                            </Tooltip.Root>
                            {deadline && (
                                <span
                                    className={cn(
                                        "inline-flex shrink-0 items-center gap-1 rounded",
                                        "border border-input px-1.5 py-0.5 text-xs text-muted-foreground"
                                    )}
                                >
                                    <IconComponent icon="calendar" size="3" />
                                    {Utils.String.formatDateLocale(deadline)}
                                </span>
                            )}
                        </Flex>
                    </Flex>
                </Flex>
                <Flex items="center" gap="1.5">
                    <Box display={{ initial: "hidden", md: "block" }}>
                        <BoardCardCheckitemTimer key={`board-card-checkitem-timer-${checkitem.uid}`} />
                    </Box>
                    {canEditCheckitem && <BoardCardCheckitemMoreMenu key={`board-card-checkitem-more-${checkitem.uid}`} />}
                </Flex>
            </Flex>
        </ModelRegistry.ProjectCheckitem.Provider>
    );
});

export default BoardCardCheckitem;
