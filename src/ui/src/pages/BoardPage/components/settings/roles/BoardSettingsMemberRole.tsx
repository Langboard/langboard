import Checkbox from "@/components/base/Checkbox";
import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import Toast from "@/components/base/Toast";
import UserAvatar from "@/components/UserAvatar";
import UserAvatarDefaultList from "@/components/UserAvatarDefaultList";
import useUpdateProjectUserRoles from "@/controllers/api/board/settings/useUpdateProjectUserRoles";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { User } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import { ROLE_ALL_GRANTED } from "@/core/models/roles/base";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { memo, useCallback } from "react";
import { useTranslation } from "react-i18next";

export interface IBoardSettingsMemberRoleProps {
    member: User.TModel;
    isValidating: bool;
    setIsValidating: React.Dispatch<React.SetStateAction<bool>>;
}

const BoardSettingsMemberRole = memo(({ member, isValidating, setIsValidating }: IBoardSettingsMemberRoleProps) => {
    const [t] = useTranslation();
    const { project } = useBoardSettings();
    const memberRoles = project.useField("member_roles");
    const roles = memberRoles?.[member.uid];
    const { mutateAsync } = useUpdateProjectUserRoles(member.uid, { interceptToast: true });
    const updateRole = useCallback(
        (e: React.MouseEvent<HTMLButtonElement>) => {
            if (isValidating) {
                return;
            }

            const role: ProjectRole.EAction = e.currentTarget.getAttribute("data-value") as ProjectRole.EAction;

            setIsValidating(true);

            const newRoles = roles.includes(ROLE_ALL_GRANTED) ? Object.values(ProjectRole.EAction) : [...roles];

            const promise = mutateAsync({
                project_uid: project.uid,
                roles: newRoles.includes(role) ? newRoles.filter((r) => r !== role) : [...newRoles, role],
            });

            Toast.Add.promise(promise, {
                loading: t("common.Updating..."),
                error: (error) => {
                    const messageRef = { message: "" };
                    const { handle } = setupApiErrorHandler({}, messageRef);

                    handle(error);
                    return messageRef.message;
                },
                success: () => {
                    return t("successes.Member roles updated successfully.");
                },
                finally: () => {
                    setIsValidating(false);
                },
            });
        },
        [isValidating, setIsValidating, memberRoles]
    );

    if (!roles) {
        return null;
    }

    return (
        <Flex items="center" justify="between" gap="3">
            <UserAvatar.Root
                userOrBot={member}
                avatarSize="xs"
                withNameProps={{
                    className: "inline-flex gap-1 select-none",
                    nameClassName: "text-base",
                }}
            >
                <UserAvatarDefaultList
                    userOrBot={member}
                    scope={{
                        projectUID: project.uid,
                    }}
                />
            </UserAvatar.Root>
            <Flex wrap gap="2">
                {Object.keys(ProjectRole.EAction).map((key) => {
                    const disabled = key === "Read" || isValidating;
                    return (
                        <Label
                            display="flex"
                            items="center"
                            key={`board-member-role-${member.uid}-${key}`}
                            className={!disabled ? "cursor-pointer" : "cursor-not-allowed"}
                        >
                            <Checkbox
                                checked={roles.includes(ProjectRole.EAction[key]) || roles.includes(ROLE_ALL_GRANTED)}
                                disabled={disabled}
                                data-value={ProjectRole.EAction[key]}
                                className="mr-1"
                                onClick={updateRole}
                            />
                            {t(`role.project.${ProjectRole.EAction[key]}`)}
                        </Label>
                    );
                })}
            </Flex>
        </Flex>
    );
});

export default BoardSettingsMemberRole;
