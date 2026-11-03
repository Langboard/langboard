import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Popover from "@/components/base/Popover";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { useBoardController } from "@/core/providers/BoardController";
import { cn } from "@/core/utils/ComponentUtils";
import BoardCardActionRelationshipSection from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationshipSection";
import { ISharedBoardCardActionProps } from "@/pages/BoardPage/components/card/action/types";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export interface IBoardCardActionRelationshipProps extends ISharedBoardCardActionProps {
    isExpanded?: bool;
}

function BoardCardActionRelationship({ buttonClassName, isExpanded = false }: IBoardCardActionRelationshipProps) {
    const { card } = useBoardCard();
    const { selectCardViewType } = useBoardController();
    const relationships = card.useForeignFieldArray("relationships");
    const [isOpened, setIsOpened] = useState(false);
    const [isValidating, setIsValidating] = useState(false);
    const [t] = useTranslation();
    const title = t("board.filters.Relationships");

    return (
        <Popover.Root open={isOpened} onOpenChange={setIsOpened}>
            <Popover.Trigger asChild>
                <Button
                    type="button"
                    variant={{ initial: "secondary", sm: "default" }}
                    size="icon"
                    title={title}
                    aria-label={title}
                    className={cn(
                        buttonClassName,
                        "transition-transform duration-200 sm:z-[120] sm:rounded-full",
                        isExpanded ? "sm:relative sm:size-9" : "sm:absolute sm:-right-5 sm:top-1/2 sm:size-10 sm:-translate-y-1/2"
                    )}
                >
                    <IconComponent icon="git-fork" className="size-4 sm:size-6" />
                    <Box display={{ sm: "hidden" }}>{title}</Box>
                </Button>
            </Popover.Trigger>
            <Popover.Content className="z-[130] max-h-[calc(100dvh-2rem)] overflow-y-auto p-0" hidden={!!selectCardViewType}>
                <Flex items="center" justify="between" textSize="base" weight="semibold" className="border-b" pl="4" mr="1" py="1">
                    {title}
                    <Button variant="ghost" size="icon-sm" aria-label={t("common.Close")} disabled={isValidating} onClick={() => setIsOpened(false)}>
                        <IconComponent icon="x" size="5" />
                    </Button>
                </Flex>
                <Flex direction="col" gap="4" p="4">
                    {(["parents", "children"] as const).map((type) => (
                        <BoardCardActionRelationshipSection
                            key={type}
                            type={type}
                            relationships={relationships}
                            onOpenChange={setIsOpened}
                            isValidating={isValidating}
                            onValidatingChange={setIsValidating}
                        />
                    ))}
                </Flex>
            </Popover.Content>
        </Popover.Root>
    );
}

export default BoardCardActionRelationship;
