import Breadcrumb from "@/components/base/Breadcrumb";
import Flex from "@/components/base/Flex";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { BotModel } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import BotApiKey from "@/pages/SettingsPage/components/bots/BotApiKey";
import BotApiURL from "@/pages/SettingsPage/components/bots/BotApiURL";
import BotAppApiToken from "@/pages/SettingsPage/components/bots/BotAppApiToken";
import BotAvatar from "@/pages/SettingsPage/components/bots/BotAvatar";
import BotDefaultTriggers from "@/pages/SettingsPage/components/bots/BotDefaultTriggers";
import BotIpWhitelist from "@/pages/SettingsPage/components/bots/BotIpWhitelist";
import BotName from "@/pages/SettingsPage/components/bots/BotName";
import BotPlatform from "@/pages/SettingsPage/components/bots/BotPlatform";
import BotPlatformRunningType from "@/pages/SettingsPage/components/bots/BotPlatformRunningType";
import BotUniqueName from "@/pages/SettingsPage/components/bots/BotUniqueName";
import BotValue from "@/pages/SettingsPage/components/bots/BotValue";
import { requirements } from "@/components/bots/BotValueInput/utils";
import { memo, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ALLOWED_ALL_IPS_BY_PLATFORMS } from "@langboard/core/ai";

export interface IBotDetailsProps {
    bot: BotModel.TModel;
}

export const BotDetailsContent = memo(({ bot }: IBotDetailsProps) => {
    const platform = bot.useField("platform");
    const runningType = bot.useField("platform_running_type");
    const formRequirements = useMemo(() => requirements[platform]?.[runningType] ?? [], [platform, runningType]);
    const canUseAllIps = ALLOWED_ALL_IPS_BY_PLATFORMS[platform]?.includes(runningType);

    return (
        <ModelRegistry.BotModel.Provider model={bot}>
            <Flex direction="col" gap="4">
                <BotAvatar />
                <Flex justify="center">
                    <Flex direction="col" gap="2" w="full" className="max-w-screen-xs">
                        <BotName />
                        <BotUniqueName />
                    </Flex>
                </Flex>
                <Flex justify="center" mt="3">
                    <Flex direction="col" gap="2" w="full" className="max-w-screen-xs">
                        <BotPlatform />
                        <BotPlatformRunningType />
                    </Flex>
                </Flex>
                <Flex justify="center" mt="3">
                    <Flex direction="col" gap="2" w="full" className="max-w-screen-xs">
                        {formRequirements.includes("url") && <BotApiURL />}
                        {formRequirements.includes("apiKey") && <BotApiKey />}
                        <BotAppApiToken />
                        {canUseAllIps && <BotIpWhitelist />}
                    </Flex>
                </Flex>
                {formRequirements.includes("value") && (
                    <Flex justify="center" mt="3">
                        <Flex justify="center" w="full" className="max-w-screen-lg">
                            <BotValue />
                        </Flex>
                    </Flex>
                )}
                <BotDefaultTriggers />
            </Flex>
        </ModelRegistry.BotModel.Provider>
    );
});
BotDetailsContent.displayName = "Settings.BotDetailsContent";

const BotDetails = memo(({ bot }: IBotDetailsProps) => {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { isValidating } = useAppSetting();
    const name = bot.useField("name");

    const moveToList = () => {
        if (isValidating) {
            return;
        }

        navigate(ROUTES.SETTINGS.BOTS, { smooth: true });
    };

    useEffect(() => {
        setPageAliasRef.current(t("settings.{botName} details", { botName: name }));
    }, [name]);

    return (
        <>
            <Breadcrumb.Root>
                <Breadcrumb.List>
                    <Breadcrumb.Item className="cursor-pointer">
                        <Breadcrumb.Link onClick={moveToList}>{t("settings.Bots")}</Breadcrumb.Link>
                    </Breadcrumb.Item>
                    <Breadcrumb.Separator />
                    <Breadcrumb.Item>
                        <Breadcrumb.Page>{t("settings.{botName} details", { botName: name })}</Breadcrumb.Page>
                    </Breadcrumb.Item>
                </Breadcrumb.List>
            </Breadcrumb.Root>
            <BotDetailsContent bot={bot} />
        </>
    );
});

export default BotDetails;
