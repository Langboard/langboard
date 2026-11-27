import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import ApiComfortToolList from "@/pages/SettingsPage/components/apiComfortTools/ApiComfortToolList";

function ApiComfortToolsPage() {
    const [t] = useTranslation();
    const { setPageAliasRef } = usePageHeader();
    const { currentUser, isValidating } = useAppSetting();
    const [createDialogOpen, setCreateDialogOpen] = useState(false);
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canCreateComfortTool = hasRoleAction(SettingRole.EAction.ApiComfortToolCreate);

    useEffect(() => {
        setPageAliasRef.current("API comfort tools");
    }, []);

    return (
        <>
            <Flex
                justify={{ sm: "between" }}
                direction={{ initial: "col", sm: "row" }}
                gap="2"
                mb="4"
                pb="2"
                textSize="3xl"
                weight="semibold"
                className="scroll-m-20 tracking-tight"
            >
                <span>{t("settings.API comfort tools")}</span>
                {canCreateComfortTool && (
                    <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={() => setCreateDialogOpen(true)}>
                        <IconComponent icon="plus" size="4" />
                        {t("settings.Add new")}
                    </Button>
                )}
            </Flex>
            <ApiComfortToolList createDialogOpen={createDialogOpen} setCreateDialogOpen={setCreateDialogOpen} />
        </>
    );
}

export default ApiComfortToolsPage;
