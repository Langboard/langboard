import Box from "@/components/base/Box";
import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import Toast from "@/components/base/Toast";
import MoreMenu from "@/components/MoreMenu";
import MultiSelect from "@/components/MultiSelect";
import { DISABLE_DRAGGING_ATTR } from "@/constants";
import useDeleteApiKey from "@/controllers/api/settings/apiKeys/useDeleteApiKey";
import useUpdateApiKey from "@/controllers/api/settings/apiKeys/useUpdateApiKey";
import setupApiErrorHandler, { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ApiKeySettingModel } from "@/core/models";
import { ApiKeyRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";
import { memo, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

export interface IApiKeyMoreMenuProps {
    apiKey: ApiKeySettingModel.TModel;
}

const ApiKeyMoreMenu = memo(({ apiKey }: IApiKeyMoreMenuProps) => {
    const { currentUser } = useAppSetting();
    const apiKeyRoleActions = currentUser.useField("api_key_role_actions");
    const { hasRoleAction } = useRoleActionFilter(apiKeyRoleActions);
    const canUpdateApiKey = hasRoleAction(ApiKeyRole.EAction.Update);
    const canDeleteApiKey = hasRoleAction(ApiKeyRole.EAction.Delete);

    if (!canUpdateApiKey && !canDeleteApiKey) {
        return null;
    }

    return (
        <MoreMenu.Root
            triggerProps={{ className: "size-7", titleSide: "bottom", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
            contentProps={{ className: "w-min p-0", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
        >
            {canUpdateApiKey && <ApiKeyMoreMenuEditIPWhitelist apiKey={apiKey} />}
            {canDeleteApiKey && <ApiKeyMoreMenuDelete apiKey={apiKey} />}
        </MoreMenu.Root>
    );
});

const ApiKeyMoreMenuEditIPWhitelist = memo(({ apiKey }: IApiKeyMoreMenuProps) => {
    const [t] = useTranslation();
    const rawIpWhitelist = apiKey.useField("ip_whitelist");
    const navigate = usePageNavigateRef();
    const ipWhitelist = useMemo(() => rawIpWhitelist.filter((ip) => ip !== ApiKeySettingModel.ALLOWED_ALL_IPS), [rawIpWhitelist]);
    const isAllAllowed = useMemo(() => rawIpWhitelist.includes(ApiKeySettingModel.ALLOWED_ALL_IPS), [rawIpWhitelist]);
    const [ipWhitelistState, setIpWhitelistState] = useState(isAllAllowed ? [] : ipWhitelist);
    const [isAllAllowedState, setIsAllAllowedState] = useState(isAllAllowed);
    const { mutateAsync } = useUpdateApiKey(apiKey, { interceptToast: true });

    const saveIPWhitelist = (endCallback: (shouldClose: bool) => void) => {
        const promise = mutateAsync({
            ip_whitelist: isAllAllowedState ? ApiKeySettingModel.ALLOWED_ALL_IPS : ipWhitelistState.join(","),
        });

        Toast.Add.promise(promise, {
            loading: t("common.Updating..."),
            error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
            success: () => {
                return t("successes.API key updated successfully.");
            },
            finally: () => {
                endCallback(true);
            },
        });
    };

    return (
        <MoreMenu.PopoverItem
            contentProps={{ align: "center", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
            menuName={t("settings.Edit IP whitelist")}
            saveText={t("common.Save")}
            onSave={saveIPWhitelist}
        >
            <Flex direction="col" gap="4" w="full">
                <MultiSelect
                    selections={[]}
                    placeholder={t("settings.Add a new IP address or range (e.g. 192.0.0.1 or 192.0.0.0/24)...")}
                    selectedValue={isAllAllowedState ? [] : ipWhitelistState}
                    onValueChange={(values) => {
                        setIpWhitelistState(values);
                    }}
                    className="w-full"
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
                    disabled={isAllAllowedState}
                />
                <Label display="flex" items="center" gap="1.5" cursor="pointer">
                    <Checkbox
                        checked={isAllAllowedState}
                        onCheckedChange={(checked) => {
                            if (Utils.Type.isString(checked)) {
                                return;
                            }

                            if (checked) {
                                setIpWhitelistState([]);
                            }

                            setIsAllAllowedState(checked);
                        }}
                    />
                    {t("settings.Allow all")}
                </Label>
            </Flex>
        </MoreMenu.PopoverItem>
    );
});

const ApiKeyMoreMenuDelete = memo(({ apiKey }: IApiKeyMoreMenuProps) => {
    const [t] = useTranslation();
    const { mutateAsync } = useDeleteApiKey(apiKey, { interceptToast: true });

    const deleteKey = (endCallback: (shouldClose: bool) => void) => {
        const promise = mutateAsync({});

        Toast.Add.promise(promise, {
            loading: t("common.Deleting..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);
                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.API key deleted successfully.");
            },
            finally: () => {
                endCallback(true);
            },
        });
    };

    return (
        <MoreMenu.PopoverItem
            modal
            contentProps={{ align: "center", ...{ [DISABLE_DRAGGING_ATTR]: "" } }}
            menuName={t("common.Delete")}
            saveText={t("common.Delete")}
            saveButtonProps={{ variant: "destructive" }}
            onSave={deleteKey}
        >
            <Box mb="1" textSize={{ initial: "sm", sm: "base" }} weight="semibold" className="text-center">
                {t("ask.Are you sure you want to delete this API key?")}
            </Box>
            <Box maxW="full" textSize="sm" weight="bold" className="text-center text-red-500">
                {t("common.deleteDescriptions.This action cannot be undone.")}
            </Box>
        </MoreMenu.PopoverItem>
    );
});

export default ApiKeyMoreMenu;
