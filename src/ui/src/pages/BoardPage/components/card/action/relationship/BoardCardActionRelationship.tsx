import { useBoardCard } from "@/core/providers/BoardCardProvider";
import BoardCardActionRelationshipButton from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationshipButton";
import { ISharedBoardCardActionProps } from "@/pages/BoardPage/components/card/action/types";

export interface IBoardCardActionRelationshipProps extends ISharedBoardCardActionProps {
    isExpanded?: bool;
}

function BoardCardActionRelationship({ buttonClassName, isExpanded = false }: IBoardCardActionRelationshipProps) {
    const { card } = useBoardCard();
    const relationships = card.useForeignFieldArray("relationships");

    return (
        <>
            <BoardCardActionRelationshipButton
                type="parents"
                relationships={relationships}
                buttonClassName={buttonClassName}
                isExpanded={isExpanded}
            />
            <BoardCardActionRelationshipButton
                type="children"
                relationships={relationships}
                buttonClassName={buttonClassName}
                isExpanded={isExpanded}
            />
        </>
    );
}

export default BoardCardActionRelationship;
