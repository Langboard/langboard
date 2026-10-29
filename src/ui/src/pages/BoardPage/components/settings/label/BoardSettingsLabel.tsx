import { renderClonedPreview } from "@/core/helpers/dnd/renderClonedPreview";
import { ProjectLabel } from "@/core/models";
import { DropIndicator } from "@atlaskit/pragmatic-drag-and-drop-react-drop-indicator/box";
import { memo, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Tooltip from "@/components/base/Tooltip";
import BoardSettingsLabelMoreMenu from "@/pages/BoardPage/components/settings/label/BoardSettingsLabelMoreMenu";
import BoardSettingsLabelColor from "@/pages/BoardPage/components/settings/label/BoardSettingsLabelColor";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { TSingleRowState } from "@/core/helpers/dnd/types";
import { SINGLE_ROW_IDLE } from "@/core/helpers/dnd/createDndSingleRowEvents";
import { singleDndHelpers } from "@/core/helpers/dnd";
import { BOARD_SETTINGS_LABEL_DND_SYMBOL_SET } from "@/pages/BoardPage/components/settings/label/BoardSettingsLabelConstants";
import invariant from "tiny-invariant";

export interface IBoardSettingsLabelProps {
    label: ProjectLabel.TModel;
}

function BoardSettingsLabel({ label }: IBoardSettingsLabelProps): React.JSX.Element {
    const [state, setState] = useState<TSingleRowState>(SINGLE_ROW_IDLE);
    const order = label.useField("order");
    const outerRef = useRef<HTMLDivElement | null>(null);
    const draggableRef = useRef<HTMLButtonElement | null>(null);

    useEffect(() => {
        const outer = outerRef.current;
        const draggable = draggableRef.current;
        invariant(outer && draggable);

        return singleDndHelpers.row({
            row: label,
            symbolSet: BOARD_SETTINGS_LABEL_DND_SYMBOL_SET,
            draggable: draggable,
            dropTarget: outer,
            setState,
            renderPreview({ container }) {
                renderClonedPreview(outer, container);
            },
        });
    }, [label, order]);

    return (
        <Box position="relative" py="1" ref={outerRef}>
            {state.type === "is-over" && <DropIndicator edge={state.closestEdge} />}
            <BoardSettingsLabelDisplay label={label} draggableRef={draggableRef} />
        </Box>
    );
}

interface IBoardSettingsLabelDisplayProps {
    label: ProjectLabel.TModel;
    draggableRef: React.RefObject<HTMLButtonElement | null>;
}

const BoardSettingsLabelDisplay = memo(({ label, draggableRef }: IBoardSettingsLabelDisplayProps) => {
    const [t] = useTranslation();
    const labelName = label.useField("name");
    const labelDescription = label.useField("description");
    const [isValidating, setIsValidating] = useState(false);

    return (
        <ModelRegistry.ProjectLabel.Provider model={label} params={{ isValidating, setIsValidating }}>
            <Flex items="center" justify="between">
                <Flex items="center" gap={{ initial: "1.5", sm: "2.5" }} className="truncate">
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="h-8 min-h-8 w-5 min-w-5 sm:size-8 sm:min-w-8"
                        title={t("common.Drag to reorder")}
                        disabled={isValidating}
                        ref={draggableRef}
                    >
                        <IconComponent icon="grip-vertical" size="4" />
                    </Button>
                    <BoardSettingsLabelColor />
                    <Flex items={{ sm: "center" }} direction={{ initial: "col", sm: "row" }} gap={{ sm: "3" }} className="truncate">
                        <Tooltip.Root>
                            <Tooltip.Trigger asChild>
                                <Box textSize="sm" className="truncate">
                                    {labelName}
                                </Box>
                            </Tooltip.Trigger>
                            <Tooltip.Content>{labelName}</Tooltip.Content>
                        </Tooltip.Root>
                        <Tooltip.Root>
                            <Tooltip.Trigger asChild>
                                <Box textSize="sm" className="truncate text-muted-foreground/70">
                                    {labelDescription}
                                </Box>
                            </Tooltip.Trigger>
                            <Tooltip.Content>{labelDescription}</Tooltip.Content>
                        </Tooltip.Root>
                    </Flex>
                </Flex>
                <BoardSettingsLabelMoreMenu />
            </Flex>
        </ModelRegistry.ProjectLabel.Provider>
    );
});

export default BoardSettingsLabel;
