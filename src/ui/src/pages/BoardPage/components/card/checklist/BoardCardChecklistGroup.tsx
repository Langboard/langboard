import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Collapsible from "@/components/base/Collapsible";
import Flex from "@/components/base/Flex";
import useChangeCardChecklistOrder from "@/controllers/api/card/checklist/useChangeCardChecklistOrder";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ProjectCheckitem, ProjectChecklist } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import BoardCardChecklist from "@/pages/BoardPage/components/card/checklist/BoardCardChecklist";
import SkeletonBoardCardCheckitem from "@/pages/BoardPage/components/card/checklist/SkeletonBoardCardCheckitem";
import { useEffect, useMemo, useReducer, useState } from "react";
import { useTranslation } from "react-i18next";
import useColumnReordered from "@/core/hooks/useColumnReordered";
import useChangeCardCheckitemOrder from "@/controllers/api/card/checkitem/useChangeCardCheckitemOrder";
import invariant from "tiny-invariant";
import { columnRowDndHelpers } from "@/core/helpers/dnd";
import { BOARD_CARD_CHECK_DND_SETTINGS, BOARD_CARD_CHECK_DND_SYMBOL_SET } from "@/pages/BoardPage/components/card/checklist/BoardCardCheckConstants";

export function SkeletonBoardCardChecklistGroup() {
    return (
        <>
            <SkeletonBoardCardCheckitem />
            <SkeletonBoardCardCheckitem />
            <SkeletonBoardCardCheckitem />
        </>
    );
}

function BoardCardChecklistGroup(): React.JSX.Element {
    const [t] = useTranslation();
    const { projectUID, card, socket, viewportRef } = useBoardCard();
    const [isOpened, setIsOpened] = useState(false);
    const flatChecklists = ProjectChecklist.Model.useModels((model) => model.card_uid === card.uid, [card]);
    const checkitemsMap = useMemo<Record<string, ProjectCheckitem.TModel>>(() => {
        const map: Record<string, ProjectCheckitem.TModel> = {};
        flatChecklists.forEach((checklist) => {
            checklist.checkitems.forEach((checkitem) => {
                map[checkitem.uid] = checkitem;
            });
        });
        return map;
    }, [flatChecklists]);
    const { mutate: changeChecklistOrderMutate } = useChangeCardChecklistOrder();
    const { mutate: changeCheckitemOrderMutate } = useChangeCardCheckitemOrder();
    const updater = useReducer((x) => x + 1, 0);
    const { columns: checklists } = useColumnReordered({
        type: "ProjectChecklist",
        eventNameParams: { uid: card.uid },
        topicId: projectUID,
        columns: flatChecklists,
        socket,
        updater,
    });

    useEffect(() => {
        const scrollable = viewportRef.current;
        invariant(scrollable);

        const setupApiErrors = (error: unknown, undo: () => void) => {
            const { handle } = setupApiErrorHandler({
                code: {
                    after: undo,
                },
                wildcard: {
                    after: undo,
                },
            });

            handle(error);
        };

        return columnRowDndHelpers.root({
            columns: checklists,
            rowsMap: checkitemsMap,
            columnKeyInRow: "checklist_uid",
            symbolSet: BOARD_CARD_CHECK_DND_SYMBOL_SET,
            scrollable,
            settings: BOARD_CARD_CHECK_DND_SETTINGS,
            isIndicator: true,
            changeColumnOrder: ({ columnUID, order, undo }) => {
                changeChecklistOrderMutate(
                    { project_uid: projectUID, card_uid: card.uid, checklist_uid: columnUID, order },
                    {
                        onError: (error) => setupApiErrors(error, undo),
                    }
                );
            },
            changeRowOrder: ({ rowUID, order, parentUID, undo }) => {
                changeCheckitemOrderMutate(
                    { project_uid: projectUID, card_uid: card.uid, checkitem_uid: rowUID, parent_uid: parentUID, order },
                    {
                        onError: (error) => setupApiErrors(error, undo),
                    }
                );
            },
        });
    }, [card, changeCheckitemOrderMutate, changeChecklistOrderMutate, checkitemsMap, checklists, projectUID]);

    return (
        <>
            {checklists.slice(0, 5).map((checklist) => (
                <BoardCardChecklist key={`board-checklist-${checklist.uid}`} checklist={checklist} checkitemsMap={checkitemsMap} />
            ))}
            {checklists.length > 5 && (
                <Collapsible.Root open={isOpened} onOpenChange={setIsOpened}>
                    <Collapsible.Content asChild>
                        <Box>
                            {checklists.slice(5).map((checklist) => (
                                <BoardCardChecklist key={`board-checklist-${checklist.uid}`} checklist={checklist} checkitemsMap={checkitemsMap} />
                            ))}
                        </Box>
                    </Collapsible.Content>
                    <Flex justify="start" mt="2">
                        <Collapsible.Trigger asChild>
                            <Button size="sm" variant="secondary">
                                {t(`card.${isOpened ? "Show fewer checklists" : "Show all checklists ({checklists} hidden)"}`, {
                                    checklists: checklists.length - 5,
                                })}
                            </Button>
                        </Collapsible.Trigger>
                    </Flex>
                </Collapsible.Root>
            )}
        </>
    );
}

export default BoardCardChecklistGroup;
