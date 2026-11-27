import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import Collaborative from "@/components/Collaborative";
import useUpdateUserInSettings from "@/controllers/api/settings/users/useUpdateUserInSettings";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import useChangeEditMode from "@/core/hooks/useChangeEditMode";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { User } from "@/core/models";
import { SettingRole } from "@/core/models/roles";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

function UserLastname({ user }: { user: User.TModel }) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser } = useAppSetting();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canUpdateUser = hasRoleAction(SettingRole.EAction.UserUpdate);
    const lastname = user.useField("lastname");
    const editorName = `${user.uid}-user-lastname`;
    const { mutateAsync } = useUpdateUserInSettings(user, { interceptToast: true });
    const resetCollaborativeLastnameRef = useRef<((value: string) => void) | null>(null);

    const { valueRef, isEditing, setIsEditing, changeMode } = useChangeEditMode({
        canEdit: () => canUpdateUser,
        valueType: "input",
        editorName,
        save: (value, endCallback) => {
            const promise = mutateAsync({
                lastname: value,
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
                    return t("successes.User last name changed successfully.");
                },
                finally: () => {
                    endCallback();
                },
            });
        },
        originalValue: lastname,
    });
    const cancelEditing = () => {
        resetCollaborativeLastnameRef.current?.(lastname);
        setIsEditing(false);
    };

    return (
        <Box className={cn("truncate text-center", isEditing && "pb-2.5 pt-[calc(theme(spacing.4)_-_2px)]")}>
            {!isEditing ? (
                <Flex cursor="pointer" justify="center" items="center" gap="1" position="relative" onClick={() => changeMode("edit")}>
                    <Box as="span" className="max-w-[calc(100%_-_theme(spacing.6))] truncate">
                        {lastname}
                    </Box>
                    <Box position="relative">
                        <Box position="absolute" left="2" className="top-1/2 -translate-y-1/2">
                            <IconComponent icon="pencil" size="4" />
                        </Box>
                    </Box>
                </Flex>
            ) : (
                <Flex items="center" gap="1">
                    <Collaborative.Input
                        collaborationType={EEditorCollaborationType.AppSettings}
                        uid={user.uid}
                        section="user"
                        field="lastname"
                        ref={valueRef}
                        className={cn(
                            "h-6 rounded-none border-x-0 border-t-0 bg-transparent p-0 text-center scrollbar-hide",
                            "focus-visible:border-b-primary focus-visible:ring-0"
                        )}
                        defaultValue={lastname}
                        onCollaborativeValueResetReady={(resetValue) => {
                            resetCollaborativeLastnameRef.current = resetValue;
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
}

export default UserLastname;
