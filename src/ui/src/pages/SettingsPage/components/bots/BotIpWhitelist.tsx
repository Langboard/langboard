import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import Toast from "@/components/base/Toast";
import MultiSelect from "@/components/MultiSelect";
import useUpdateBot from "@/controllers/api/settings/bots/useUpdateBot";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { BotModel } from "@/core/models";

const BotIpWhitelist = memo(() => {
    const [t] = useTranslation();
    const { model: bot } = ModelRegistry.BotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateBot = hasRoleAction(SettingRole.EAction.BotUpdate);
    const rawIpWhitelist = bot.useField("ip_whitelist");
    const ipWhitelist = useMemo(() => rawIpWhitelist.filter((ip) => ip !== BotModel.ALLOWED_ALL_IPS), [rawIpWhitelist]);
    const [isValidating, setIsValidating] = useState(false);
    const isAllAllowed = useMemo(() => rawIpWhitelist.includes(BotModel.ALLOWED_ALL_IPS), [rawIpWhitelist]);
    const { mutateAsync } = useUpdateBot(bot, { interceptToast: true });
    const updateBot = (values: string[]) => {
        if (isValidating || !canUpdateBot) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            ip_whitelist: values,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.Bot IP whitelist changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Flex items="center" gap="2">
            <MultiSelect
                selections={[]}
                placeholder={t("settings.Add a new IP address or range (e.g. 192.0.0.1 or 192.0.0.0/24)...")}
                selectedValue={isAllAllowed ? [] : ipWhitelist}
                onValueChange={updateBot}
                className="w-[calc(100%_-_theme(spacing.24))]"
                inputClassName="ml-1 placeholder:text-gray-500 placeholder:font-medium"
                canCreateNew
                validateCreatedNewValue={Utils.String.isValidIpv4OrRange}
                createNewCommandItemLabel={(value) => {
                    const newIPs: string[] = [];

                    if (value.includes("/24")) {
                        newIPs.push(value, value.replace("/24", ""));
                    } else {
                        newIPs.push(value, `${value}/24`);
                    }

                    return newIPs.map((ip) => ({
                        label: ip,
                        value: ip,
                    }));
                }}
                isNewCommandItemMultiple
                disabled={!canUpdateBot || isValidating || isAllAllowed}
            />
            <Label display="flex" items="center" gap="1.5" w="20" mb="2" cursor={canUpdateBot ? "pointer" : "default"}>
                <Checkbox
                    checked={isAllAllowed}
                    onCheckedChange={(checked) => {
                        if (Utils.Type.isString(checked) || !canUpdateBot) {
                            return;
                        }

                        if (checked) {
                            updateBot([BotModel.ALLOWED_ALL_IPS]);
                        } else {
                            updateBot([]);
                        }
                    }}
                    disabled={!canUpdateBot}
                />
                {t("settings.Allow all")}
            </Label>
        </Flex>
    );
});

export default BotIpWhitelist;
