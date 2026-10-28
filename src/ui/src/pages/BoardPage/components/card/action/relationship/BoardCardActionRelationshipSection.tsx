import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import Toast from "@/components/base/Toast";
import useUpdateCardRelationships from "@/controllers/api/card/useUpdateCardRelationships";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { ProjectCardRelationship } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { useBoardController } from "@/core/providers/BoardController";
import BoardCardActionRelationshipList from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationshipList";
import { useTranslation } from "react-i18next";

interface IBoardCardActionRelationshipSectionProps {
    type: ProjectCardRelationship.TRelationship;
    relationships: ProjectCardRelationship.TModel[];
    onOpenChange: (open: boolean) => void;
    isValidating: boolean;
    onValidatingChange: (pending: boolean) => void;
}

function BoardCardActionRelationshipSection({
    type,
    relationships,
    onOpenChange,
    isValidating,
    onValidatingChange,
}: IBoardCardActionRelationshipSectionProps) {
    const { disabledCardSelectionUIDsRef, startCardSelection, filterRelationships } = useBoardController();
    const { projectUID, card } = useBoardCard();
    const { mutateAsync: updateCardRelationshipsMutateAsync } = useUpdateCardRelationships({ interceptToast: true });
    const [t] = useTranslation();
    const isParent = type === "parents";

    const saveRelationship = (newRelationships: [string, string][]) => {
        onOpenChange(true);
        onValidatingChange(true);

        const promise = updateCardRelationshipsMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            is_parent: isParent,
            relationships: newRelationships,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Updating..."),
            error: createApiErrorMessageHandler(),
            success: () => {
                return t("successes.Relationships updated successfully.");
            },
            finally: () => {
                onValidatingChange(false);
            },
        });
    };

    const selectRelationship = () => {
        const initialSelections = filterRelationships(card.uid, relationships, isParent).map(
            (relationship) =>
                [isParent ? relationship.parent_card_uid : relationship.child_card_uid, relationship.relationship_type_uid] satisfies [string, string]
        );

        onOpenChange(false);
        // Reverse composition/reference edges are valid; the server rejects only blocks cycles.
        disabledCardSelectionUIDsRef.current = [card.uid];
        startCardSelection({
            type,
            currentUID: card.uid,
            initialSelections,
            saveCallback: saveRelationship,
            cancelCallback: () => onOpenChange(true),
        });
    };

    const title = t(`card.${type === "parents" ? "Parent" : "Child"} cards`);

    return (
        <section aria-label={title}>
            <Box textSize="sm" weight="semibold" mb="2">
                {title}
            </Box>
            <BoardCardActionRelationshipList type={type} relationships={relationships} />
            <Flex items="center" justify="end" gap="1" mt="2">
                <Button type="button" size="sm" disabled={isValidating} onClick={selectRelationship}>
                    {t("card.Select cards")}
                </Button>
            </Flex>
        </section>
    );
}

export default BoardCardActionRelationshipSection;
