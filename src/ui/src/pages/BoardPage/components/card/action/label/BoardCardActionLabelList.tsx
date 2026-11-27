import Checkbox from "@/components/base/Checkbox";
import { ICollaborativeTextMeta } from "@/components/Collaborative/useCollaborativeText";
import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import ScrollArea from "@/components/base/ScrollArea";
import { ProjectLabel } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import BoardCardActionLabel from "@/pages/BoardPage/components/card/action/label/BoardCardActionLabel";
import { memo } from "react";

interface ILabelToggleMeta {
    checked: bool;
    labelUID: string;
    updatedAt: number;
}

export interface IBoardCardActionLabelListProps {
    disabled?: bool;
    remoteLabelStates: Record<string, ICollaborativeTextMeta<ILabelToggleMeta>>;
    selectedLabelUIDs: string[];
    setSelectedLabelUIDs: React.Dispatch<React.SetStateAction<string[]>>;
}

const BoardCardActionLabelList = memo(({ disabled, remoteLabelStates, selectedLabelUIDs, setSelectedLabelUIDs }: IBoardCardActionLabelListProps) => {
    const { card } = useBoardCard();
    const flatProjectLabels = ProjectLabel.Model.useModels((model) => model.project_uid === card.project_uid);
    const projectLabels = flatProjectLabels.sort((a, b) => a.order - b.order);
    ProjectLabel.Model.subscribe("DELETION", `board-card-action-label-list-${card.uid}`, (uids) => {
        setSelectedLabelUIDs((prev) => prev.filter((uid) => !uids.includes(uid)));
    });

    const changeSelectedState = (labelUID: string) => {
        if (disabled) {
            return;
        }

        if (selectedLabelUIDs.includes(labelUID)) {
            setSelectedLabelUIDs((prev) => prev.filter((uid) => uid !== labelUID));
        } else {
            setSelectedLabelUIDs((prev) => [...prev, labelUID]);
        }
    };

    return (
        <ScrollArea.Root className="border border-dashed">
            <Flex direction="col" position="relative" className="h-[min(theme(spacing.48),35vh)] select-none">
                {projectLabels.map((label) => {
                    const remoteLabelState = remoteLabelStates[label.uid];
                    const remoteColor = remoteLabelState?.color;
                    const remoteName = remoteLabelState?.name;

                    return (
                        <Label
                            key={`board-card-action-label-${label.uid}`}
                            display="flex"
                            items="center"
                            gap="3"
                            p="2"
                            cursor="pointer"
                            className="hover:bg-secondary/40"
                            style={remoteColor ? { backgroundColor: `${remoteColor}14`, boxShadow: `inset 0 0 0 1px ${remoteColor}` } : undefined}
                            title={remoteName ? `${remoteName}` : undefined}
                        >
                            <Checkbox
                                checked={selectedLabelUIDs.includes(label.uid)}
                                disabled={disabled}
                                onCheckedChange={() => changeSelectedState(label.uid)}
                                style={remoteColor ? { borderColor: remoteColor, boxShadow: `0 0 0 1px ${remoteColor}` } : undefined}
                            />
                            <Flex items="center" justify="between" className="min-w-0 flex-1 gap-2">
                                <BoardCardActionLabel label={label} />
                                {remoteName ? (
                                    <span
                                        className="shrink-0 truncate rounded px-1.5 py-0.5"
                                        style={{
                                            backgroundColor: remoteColor ? `${remoteColor}14` : undefined,
                                            color: remoteColor || undefined,
                                            fontSize: "0.75rem",
                                            lineHeight: "1rem",
                                        }}
                                    >
                                        {remoteName}
                                    </span>
                                ) : null}
                            </Flex>
                        </Label>
                    );
                })}
            </Flex>
        </ScrollArea.Root>
    );
});

export default BoardCardActionLabelList;
