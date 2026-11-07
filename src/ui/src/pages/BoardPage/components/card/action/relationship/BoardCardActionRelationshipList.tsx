import Flex from "@/components/base/Flex";
import ScrollArea from "@/components/base/ScrollArea";
import { ProjectCardRelationship } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { useBoardController } from "@/core/providers/BoardController";
import BoardCardActionRelationshipItem from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationshipItem";
import { RELATIONSHIP_DISPLAY_GROUPS } from "@/pages/BoardPage/components/board/RelationshipTypePicker";
import { memo, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export interface IBoardCardActionRelationshipListProps {
    type: ProjectCardRelationship.TRelationship;
    relationships: ProjectCardRelationship.TModel[];
}

const BoardCardActionRelationshipList = memo(({ type, relationships: flatRelationships }: IBoardCardActionRelationshipListProps) => {
    const isParent = type === "parents";
    const { card } = useBoardCard();
    const { filterRelationships } = useBoardController();
    const [t] = useTranslation();
    const [relationships, setRelationships] = useState(filterRelationships(card.uid, flatRelationships, isParent));

    useEffect(() => {
        setRelationships(() => filterRelationships(card.uid, flatRelationships, isParent));
    }, [card, filterRelationships, flatRelationships, isParent]);

    return (
        <ScrollArea.Root className="border">
            <Flex direction="col" position="relative" textSize="sm" className="h-[min(theme(spacing.48),35vh)] select-none">
                {RELATIONSHIP_DISPLAY_GROUPS.map(({ semantic, label, hint, Icon }) => {
                    const matches = relationships.filter((relationship) => (relationship.relationship_type.machine_semantic ?? null) === semantic);
                    if (!matches.length) return null;
                    return (
                        <section key={semantic ?? "legacy"} aria-label={t(label)}>
                            <h3 className="flex items-center gap-2 border-b px-2 py-1 text-xs font-semibold text-muted-foreground" title={t(hint)}>
                                <Icon size={16} aria-hidden="true" />
                                {t(label)}
                            </h3>
                            {matches.map((relationship) => (
                                <BoardCardActionRelationshipItem
                                    key={`card-relationship-${relationship.uid}`}
                                    type={type}
                                    relationship={relationship}
                                />
                            ))}
                        </section>
                    );
                })}
            </Flex>
        </ScrollArea.Root>
    );
});

export default BoardCardActionRelationshipList;
