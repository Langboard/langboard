import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useGetBots from "@/controllers/api/settings/bots/useGetBots";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { BotModel } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import BotDetails from "@/pages/SettingsPage/components/bots/BotDetails";
import BotList from "@/pages/SettingsPage/components/bots/BotList";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";

function BotsPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { isValidating, currentUser } = useAppSetting();
    const { botUID } = useParams();
    const [bot, setBot] = useState<BotModel.TModel | null>(null);
    const [mounted, setMounted] = useState(false);
    const { mutateAsync: getBotsMutateAsync } = useGetBots();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);

    useEffect(() => {
        const initialize = async () => {
            await getBotsMutateAsync({});
            setMounted(true);
        };

        initialize();
    }, []);

    useEffect(() => {
        if (!mounted) {
            return;
        }

        if (!botUID) {
            setPageAliasRef.current("Bots");
            setBot(null);
            return;
        }

        const bot = BotModel.Model.getModel(botUID);
        if (!bot) {
            Toast.Add.error(t("errors.requests.NF3001"));
            navigate(ROUTES.SETTINGS.BOTS);
            return;
        }

        setBot(bot);
    }, [mounted, botUID]);

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_BOT);
    };

    if (!mounted) {
        return null;
    }

    return (
        <>
            {bot ? (
                <BotDetails bot={bot} />
            ) : (
                <>
                    <Flex justify="between" mb="4" pb="2" textSize="2xl" weight="semibold" className="scroll-m-20 flex-wrap gap-3 border-b">
                        <span className="w-36">{t("settings.Bots")}</span>
                        {hasRoleAction(SettingRole.EAction.InternalBotCreate) && (
                            <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                                <IconComponent icon="plus" size="4" />
                                {t("settings.Add new")}
                            </Button>
                        )}
                    </Flex>
                    <BotList />
                </>
            )}
        </>
    );
}

export default BotsPage;
