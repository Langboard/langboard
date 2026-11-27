import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Drawer from "@/components/base/Drawer";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import { ProjectRole } from "@/core/models/roles";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { BoardBotScopeList } from "@/pages/BoardPage/components/board/BoardBotScope";
import { ISharedBoardCardActionProps } from "@/pages/BoardPage/components/card/action/types";
import { memo } from "react";
import { useTranslation } from "react-i18next";

export interface IBoardCardActionBotScopeProps extends ISharedBoardCardActionProps {}

const BoardCardActionBotScope = memo(({ buttonClassName }: IBoardCardActionBotScopeProps) => {
    const { card, hasRoleAction } = useBoardCard();
    const [t] = useTranslation();

    if (!hasRoleAction(ProjectRole.EAction.Update)) {
        return null;
    }

    return (
        <Drawer.Root handleOnly repositionInputs={false}>
            <Drawer.Trigger asChild>
                <Button variant="secondary" className={buttonClassName}>
                    <IconComponent icon="bot" size="4" />
                    {t("bot.Scope bot")}
                </Button>
            </Drawer.Trigger>
            <Drawer.Content withGrabber={false} className="rounded-t-none border-none bg-transparent" aria-describedby="" focusGuards={false}>
                <Drawer.Title hidden />
                <Flex
                    direction="col"
                    position="relative"
                    mx="auto"
                    w="full"
                    pt="4"
                    pb="1"
                    border
                    className="max-w-[100vw] rounded-t-[10px] bg-background sm:max-w-screen-sm lg:max-w-screen-md"
                    data-card-comment-form
                >
                    <Drawer.Handle className="flex h-2 !w-full cursor-grab justify-center !bg-transparent py-3 text-center">
                        <Box display="inline-block" h="2" rounded="full" className="w-[100px] bg-muted" />
                    </Drawer.Handle>
                    <BoardBotScopeList target={{ target_table: "card", target: card }} className="max-h-[50vh] pb-3" />
                </Flex>
            </Drawer.Content>
        </Drawer.Root>
    );
});

export default BoardCardActionBotScope;
