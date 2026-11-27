import Flex from "@/components/base/Flex";
import { User } from "@/core/models";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import BoardSettingsMemberRole from "@/pages/BoardPage/components/settings/roles/BoardSettingsMemberRole";
import { memo, useState } from "react";

const BoardSettingsMemberRoleList = memo(() => {
    const { project } = useBoardSettings();
    const [isValidating, setIsValidating] = useState(false);
    const memberRoles = project.useField("member_roles");
    const members = User.Model.useModels(
        (model) => model.isValidUser() && !!memberRoles?.[model.uid] && model.uid !== project.owner_uid,
        [memberRoles, project]
    );

    return (
        <Flex direction="col" gap="2" py="4">
            {members.map((member) => (
                <BoardSettingsMemberRole member={member} isValidating={isValidating} setIsValidating={setIsValidating} key={member.uid} />
            ))}
        </Flex>
    );
});

export default BoardSettingsMemberRoleList;
