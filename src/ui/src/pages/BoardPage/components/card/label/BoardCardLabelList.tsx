import { latestLabelStates } from "./LabelCollaboration";
import { useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import { LabelModelBadge } from "@/components/LabelBadge";
import { ProjectLabel } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import BoardCardActionSetLabel from "@/pages/BoardPage/components/card/action/label/BoardCardActionSetLabel";

function BoardCardLabelList(): React.JSX.Element {
    const { card } = useBoardCard();
    const labels = card.useForeignFieldArray("labels");
    const { remoteMeta } = useCollaborativeText({
        defaultValue: JSON.stringify(labels.map((label) => label.uid)),
        collaborationType: EEditorCollaborationType.Card,
        uid: card.uid,
        section: "labels",
        field: "selected-label-uids",
    });
    const remoteLabelStates = latestLabelStates(remoteMeta);

    ProjectLabel.Model.subscribe("DELETION", `board-card-label-list-${card.uid}`, (uids) => {
        const newLabels = labels.filter((label) => !uids.includes(label.uid));
        card.labels = newLabels;
    });

    return (
        <Flex inline wrap gap="1.5">
            {labels.map((label) => {
                const remoteLabelState = remoteLabelStates[label.uid];

                return (
                    <Flex key={`board-card-label-${label.uid}`} inline items="center" gap="1.5">
                        <LabelModelBadge model={label} />
                        {!!remoteLabelState?.name && (
                            <Box
                                textSize="xs"
                                className="rounded px-1.5 py-0.5"
                                style={{
                                    backgroundColor: `${remoteLabelState.color}14`,
                                    color: remoteLabelState.color,
                                }}
                            >
                                {remoteLabelState.name}
                            </Box>
                        )}
                    </Flex>
                );
            })}
            <BoardCardActionSetLabel buttonClassName="h-7 gap-1 rounded-full border-dashed px-2.5 text-xs text-muted-foreground" />
        </Flex>
    );
}

export default BoardCardLabelList;
