import Box from "@/components/base/Box";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import Toast from "@/components/base/Toast";
import { ApiKeySettingModel } from "@/core/models";
import { ApiKeyRole } from "@/core/models/roles";
import useUpdateApiKey from "@/controllers/api/settings/apiKeys/useUpdateApiKey";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { EHttpStatus } from "@langboard/core/enums";
import { useTranslation } from "react-i18next";

export interface IApiKeyNameProps {
    apiKey: ApiKeySettingModel.TModel;
}

function ApiKeyName({ apiKey }: IApiKeyNameProps) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const apiKeyRoleActions = currentUser.useField("api_key_role_actions");
    const { hasRoleAction } = useRoleActionFilter(apiKeyRoleActions);
    const canUpdateApiKey = hasRoleAction(ApiKeyRole.EAction.Update);
    const name = apiKey.useField("name");
    const editorName = `${apiKey.uid}-api-key-name`;
    const { mutateAsync } = useUpdateApiKey(apiKey);

    const { valueRef, isEditing, changeMode } = useChangeEditMode({
        canEdit: () => canUpdateApiKey,
        valueType: "input",
        editorName,
        save: (value, endCallback) => {
            const promise = mutateAsync({
                name: value,
            });

            Toast.Add.promise(promise, {
                loading: t("common.Changing..."),
                error: createApiErrorMessageHandler(() => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true })),
                success: () => {
                    return t("successes.API key name changed successfully.");
                },
                finally: () => {
                    endCallback();
                },
            });
        },
        originalValue: name,
    });

    return (
        <Box className="text-center">
            {!isEditing ? (
                <Flex
                    cursor={canUpdateApiKey ? "pointer" : "default"}
                    justify="center"
                    items="center"
                    gap="1"
                    position="relative"
                    onClick={() => changeMode("edit")}
                >
                    <Box as="span" className="max-w-[calc(100%_-_theme(spacing.6))] truncate">
                        {name}
                    </Box>
                    {canUpdateApiKey && (
                        <Box position="relative">
                            <Box position="absolute" left="2" className="top-1/2 -translate-y-1/2">
                                <IconComponent icon="pencil" size="4" />
                            </Box>
                        </Box>
                    )}
                </Flex>
            ) : (
                <Input
                    ref={valueRef}
                    className={cn(
                        "h-6 rounded-none border-x-0 border-t-0 bg-transparent p-0 text-center scrollbar-hide",
                        "focus-visible:border-b-primary focus-visible:ring-0"
                    )}
                    defaultValue={name}
                    onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                    }}
                    onBlur={() => changeMode("view")}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") {
                            e.preventDefault();
                            e.stopPropagation();
                            changeMode("view");
                        }
                    }}
                />
            )}
        </Box>
    );
}

export default ApiKeyName;
