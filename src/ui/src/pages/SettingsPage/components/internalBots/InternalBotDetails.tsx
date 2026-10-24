import Breadcrumb from "@/components/base/Breadcrumb";
import Flex from "@/components/base/Flex";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { InternalBotModel } from "@/core/models";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import InternalBotApiKey from "@/pages/SettingsPage/components/internalBots/InternalBotApiKey";
import InternalBotApiURL from "@/pages/SettingsPage/components/internalBots/InternalBotApiURL";
import InternalBotAvatar from "@/pages/SettingsPage/components/internalBots/InternalBotAvatar";
import InternalBotDefault from "@/pages/SettingsPage/components/internalBots/InternalBotDefault";
import InternalBotDisplayName from "@/pages/SettingsPage/components/internalBots/InternalBotDisplayName";
import InternalBotPlatform from "@/pages/SettingsPage/components/internalBots/InternalBotPlatform";
import InternalBotPlatformRunningType from "@/pages/SettingsPage/components/internalBots/InternalBotPlatformRunningType";
import InternalBotType from "@/pages/SettingsPage/components/internalBots/InternalBotType";
import InternalBotValue from "@/pages/SettingsPage/components/internalBots/InternalBotValue";
import ImageGenerationSettings from "./ImageGenerationSettings";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { requirements } from "@/components/bots/BotValueInput/utils";
import { memo, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";

export interface IInternalBotDetailsProps {
    internalBot: InternalBotModel.TModel;
}

const InternalBotDetails = memo(({ internalBot }: IInternalBotDetailsProps) => {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { isValidating, currentUser } = useAppSetting();
    const roleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(roleActions);
    const botType = internalBot.useField("bot_type");
    const isImageGeneration = botType === InternalBotModel.EInternalBotType.ImageGeneration;
    const isNativeOnly =
        isImageGeneration ||
        botType === InternalBotModel.EInternalBotType.CommentCompaction ||
        botType === InternalBotModel.EInternalBotType.AvatarSummary;
    const displayName = internalBot.useField("display_name");
    const platform = internalBot.useField("platform");
    const runningType = internalBot.useField("platform_running_type");
    const formRequirements = useMemo(() => requirements[platform]?.[runningType] ?? [], [platform, runningType]);

    const moveToList = () => {
        if (isValidating) {
            return;
        }

        navigate(ROUTES.SETTINGS.INTERNAL_BOTS, { smooth: true });
    };

    useEffect(() => {
        setPageAliasRef.current(t("settings.{botName} details", { botName: displayName }));
    }, [displayName]);

    return (
        <ModelRegistry.InternalBotModel.Provider model={internalBot}>
            <Breadcrumb.Root>
                <Breadcrumb.List>
                    <Breadcrumb.Item className="cursor-pointer">
                        <Breadcrumb.Link onClick={moveToList}>{t("settings.Internal bots")}</Breadcrumb.Link>
                    </Breadcrumb.Item>
                    <Breadcrumb.Separator />
                    <Breadcrumb.Item>
                        <Breadcrumb.Page>{t("settings.{botName} details", { botName: displayName })}</Breadcrumb.Page>
                    </Breadcrumb.Item>
                </Breadcrumb.List>
            </Breadcrumb.Root>
            <Flex direction="col" gap="4">
                <InternalBotAvatar />
                <Flex justify="center">
                    <Flex direction="col" gap="2" w="full" className="max-w-screen-xs">
                        <InternalBotDisplayName />
                        <Flex gap="2" items="center">
                            <InternalBotType />
                            <InternalBotDefault />
                        </Flex>
                    </Flex>
                </Flex>
                {!isNativeOnly && (
                    <Flex justify="center" mt="3">
                        <Flex direction="col" gap="2" w="full" className="max-w-screen-xs">
                            <InternalBotPlatform />
                            <InternalBotPlatformRunningType />
                            {formRequirements.includes("url") && <InternalBotApiURL />}
                            {formRequirements.includes("apiKey") && <InternalBotApiKey />}
                        </Flex>
                    </Flex>
                )}
                {isImageGeneration && (
                    <Flex justify="center" mt="3">
                        <ImageGenerationSettings
                            key={internalBot.uid}
                            bot={internalBot}
                            canUpdate={hasRoleAction(SettingRole.EAction.InternalBotUpdate)}
                        />
                    </Flex>
                )}
                {botType === InternalBotModel.EInternalBotType.AvatarSummary && (
                    <p className="text-sm">{t("internalBot.avatar.summary_configuration")}</p>
                )}
                {!isImageGeneration && formRequirements.includes("value") && (
                    <Flex justify="center" mt="3">
                        <Flex justify="center" w="full" className="max-w-screen-lg">
                            <InternalBotValue />
                        </Flex>
                    </Flex>
                )}
            </Flex>
        </ModelRegistry.InternalBotModel.Provider>
    );
});

export default InternalBotDetails;
