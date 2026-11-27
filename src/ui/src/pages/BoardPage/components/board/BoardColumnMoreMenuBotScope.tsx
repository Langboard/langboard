import MoreMenu from "@/components/MoreMenu";
import { DISABLE_DRAGGING_ATTR } from "@/constants";
import { ProjectColumn } from "@/core/models";
import { BoardBotScopeList } from "@/pages/BoardPage/components/board/BoardBotScope";
import { memo } from "react";
import { useTranslation } from "react-i18next";

const BoardColumnMoreMenuBotScope = memo(({ column }: { column: ProjectColumn.TModel }) => {
    const [t] = useTranslation();

    return (
        <MoreMenu.DrawerItem
            contentProps={{ [DISABLE_DRAGGING_ATTR]: "" } as React.ComponentProps<typeof MoreMenu.DrawerItem>["contentProps"]}
            menuName={t("bot.Scope bot")}
            useButtons={false}
        >
            <BoardBotScopeList target={{ target_table: "project_column", target: column }} className="max-h-[50vh] pb-3" />
        </MoreMenu.DrawerItem>
    );
});
BoardColumnMoreMenuBotScope.displayName = "Board.ColumnMoreMenuBotScope";

export default BoardColumnMoreMenuBotScope;
