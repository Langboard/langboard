import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import Collaborative from "@/components/Collaborative";
import useUpdateInternalBot from "@/controllers/api/settings/internalBots/useUpdateInternalBot";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { memo, useRef } from "react";
import { useTranslation } from "react-i18next";

const InternalBotDisplayName = memo(() => {
    const [t] = useTranslation();
    const { model: internalBot } = ModelRegistry.InternalBotModel.useContext();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateInternalBot = hasRoleAction(SettingRole.EAction.InternalBotUpdate);
    const displayName = internalBot.useField("display_name");
    const editorName = `${internalBot.uid}-internal-bot-display-name`;
    const { mutateAsync } = useUpdateInternalBot(internalBot, { interceptToast: true });
    const resetCollaborativeDisplayNameRef = useRef<((value: string) => void) | null>(null);

    const { valueRef, isEditing, setIsEditing, changeMode } = useChangeEditMode({
        canEdit: () => canUpdateInternalBot,
        valueType: "input",
        editorName,
        save: (value, endCallback) => {
            const promise = mutateAsync({
                display_name: value,
            });

            Toast.Add.promise(promise, {
                loading: t("common.Changing..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler(
                        {
                            [EHttpStatus.HTTP_403_FORBIDDEN]: {
                                after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                            },
                        },
                        messageRef
                    );

                    handle(error);
                    return messageRef.message;
                },
                success: () => {
                    return t("successes.Internal bot display name changed successfully.");
                },
                finally: () => {
                    endCallback();
                },
            });
        },
        originalValue: displayName,
    });
    const cancelEditing = () => {
        resetCollaborativeDisplayNameRef.current?.(displayName);
        setIsEditing(false);
    };

    return (
        <Box>
            {!isEditing ? (
                <Flex
                    items="center"
                    cursor={canUpdateInternalBot ? "pointer" : "default"}
                    textSize="lg"
                    weight="semibold"
                    onClick={() => changeMode("edit")}
                >
                    <Box as="span" className="max-w-[calc(100%_-_theme(spacing.6))] truncate">
                        {displayName}
                    </Box>
                    {canUpdateInternalBot && <IconComponent icon="pencil" size="4" className="ml-2" />}
                </Flex>
            ) : (
                <Flex items="center" gap="1">
                    <Collaborative.Input
                        collaborationType={EEditorCollaborationType.AppSettings}
                        uid={internalBot.uid}
                        section="internal-bot"
                        field="display_name"
                        ref={valueRef}
                        className={cn(
                            "h-7 rounded-none border-x-0 border-t-0 bg-transparent p-0 text-lg font-semibold scrollbar-hide",
                            "focus-visible:border-b-primary focus-visible:ring-0"
                        )}
                        defaultValue={displayName}
                        onCollaborativeValueResetReady={(resetValue) => {
                            resetCollaborativeDisplayNameRef.current = resetValue;
                        }}
                        onClick={(e) => {
                            e.preventDefault();
                            e.stopPropagation();
                        }}
                        onKeyDown={(e) => {
                            if (e.key === "Enter") {
                                e.preventDefault();
                                e.stopPropagation();
                                changeMode("view");
                                return;
                            }
                        }}
                    />
                    <Button type="button" size="icon-sm" variant="ghost" onClick={() => changeMode("view")} title={t("common.Save")}>
                        <IconComponent icon="check" size="4" />
                    </Button>
                    <Button type="button" size="icon-sm" variant="ghost" onClick={cancelEditing} title={t("common.Cancel")}>
                        <IconComponent icon="x" size="4" />
                    </Button>
                </Flex>
            )}
        </Box>
    );
});

export default InternalBotDisplayName;
