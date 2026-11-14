import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useGetInternalBots from "@/controllers/api/settings/internalBots/useGetInternalBots";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { InternalBotModel } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import InternalBotDetails from "@/pages/SettingsPage/components/internalBots/InternalBotDetails";
import InternalBotList from "@/pages/SettingsPage/components/internalBots/InternalBotList";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";

function InternalBotsPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { isValidating, currentUser } = useAppSetting();
    const { botUID } = useParams();
    const [bot, setBot] = useState<InternalBotModel.TModel | null>(null);
    const [mounted, setMounted] = useState(false);
    const { mutateAsync: getInternalBotsMutateAsync } = useGetInternalBots();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);

    useEffect(() => {
        const initialize = async () => {
            await getInternalBotsMutateAsync({});
            setMounted(true);
        };

        initialize();
    }, []);

    useEffect(() => {
        if (!mounted) {
            return;
        }

        if (!botUID) {
            setPageAliasRef.current("Internal Bots");
            setBot(null);
            return;
        }

        const targetBot = InternalBotModel.Model.getModel(botUID);
        if (!targetBot) {
            Toast.Add.error(t("errors.requests.NF3004"));
            navigate(ROUTES.SETTINGS.INTERNAL_BOTS);
            return;
        }

        setBot(targetBot);
    }, [mounted, botUID]);

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_INTERNAL_BOT);
    };

    if (!mounted) {
        return null;
    }

    return (
        <>
            {bot ? (
                <InternalBotDetails internalBot={bot} />
            ) : (
                <>
                    <Flex justify="between" mb="4" pb="2" textSize="2xl" weight="semibold" className="scroll-m-20 flex-wrap gap-3 border-b">
                        <span className="max-w-72 truncate">{t("settings.Internal bots")}</span>
                        {hasRoleAction(SettingRole.EAction.InternalBotCreate) && (
                            <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                                <IconComponent icon="plus" size="4" />
                                {t("settings.Add new")}
                            </Button>
                        )}
                    </Flex>
                    <InternalBotList />
                </>
            )}
        </>
    );
}

export default InternalBotsPage;
