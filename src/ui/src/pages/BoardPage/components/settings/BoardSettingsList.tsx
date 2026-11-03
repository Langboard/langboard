import Flex from "@/components/base/Flex";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";

import BoardSettingsSection from "@/pages/BoardPage/components/settings/BoardSettingsSection";

import { lazy, memo, Suspense, useMemo, useState } from "react";
import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";

const BoardSettingsTeams = lazy(() => import("./BoardSettingsTeams"));
const BoardSettingsApps = lazy(() => import("./BoardSettingsApps"));
const BoardSettingsBasic = lazy(() => import("./BoardSettingsBasic"));
const BoardSettingsDock = lazy(() => import("./BoardSettingsDock"));
const BoardSettingsEmailNotifications = lazy(() => import("./BoardSettingsEmailNotifications"));
const BoardSettingsExecutionBindingStatus = lazy(() => import("./BoardSettingsExecutionBindingStatus"));
const BoardSettingsOther = lazy(() => import("./BoardSettingsOther"));
const BoardSettingsChatTemplateList = lazy(() => import("./chat/BoardSettingsChatTemplateList"));
const BoardSettingsInternalBotList = lazy(() => import("./internalBots/BoardSettingsInternalBotList"));
const BoardSettingsLabelList = lazy(() => import("./label/BoardSettingsLabelList"));
const BoardSettingsMemberRoleList = lazy(() => import("./roles/BoardSettingsMemberRoleList"));

export function SkeletonSettingsList() {
    return <></>;
}

const BoardSettingsList = memo(({ notifications }: { notifications?: React.ReactNode }) => {
    const [t] = useTranslation();
    const [section, setSection] = useState("board");
    const [peopleTab, setPeopleTab] = useState("participants");
    const [teamsVisited, setTeamsVisited] = useState(false);
    const [visited, setVisited] = useState(["board"]);
    const { project, currentUser } = useBoardSettings();
    const ownerUID = project.useField("owner_uid");
    const allMembers = project.useForeignFieldArray("all_members");
    const invitedMemberUIDs = project.useField("invited_member_uids");
    const isAdmin = currentUser.useField("is_admin");
    const numMembers = useMemo(
        () =>
            allMembers.filter((m) => m.isValidUser() && m.uid !== ownerUID && m.uid !== currentUser.uid && !invitedMemberUIDs.includes(m.uid)).length,
        [allMembers, ownerUID, currentUser, invitedMemberUIDs]
    );

    return (
        <Flex direction="col" gap="3" p={{ initial: "4", md: "6", lg: "8" }}>
            <nav aria-label={t("board.Settings")} className="flex flex-wrap gap-2 border-b pb-3">
                {[
                    ["board", "project.settings.Basic info"],
                    ["apps", "project.settings.Apps"],
                    ["people", "settings.People and notifications"],
                    ["automation", "settings.AI and automation"],
                ].map(([key, label]) => (
                    <Button
                        key={key}
                        size="sm"
                        variant={section === key ? "secondary" : "ghost"}
                        aria-pressed={section === key}
                        onClick={() => {
                            setSection(key);
                            setVisited((previous) => (previous.includes(key) ? previous : [...previous, key]));
                        }}
                    >
                        {t(label)}
                    </Button>
                ))}
            </nav>
            <Suspense fallback={<p role="status">{t("common.Loading...")}</p>}>
                {visited.includes("board") && (
                    <div hidden={section !== "board"} className="space-y-6">
                        <BoardSettingsSection title="project.settings.Basic info">
                            <BoardSettingsBasic />
                        </BoardSettingsSection>
                        <BoardSettingsSection title="board.Shared dock">
                            <BoardSettingsDock key={project.uid} />
                        </BoardSettingsSection>
                        <BoardSettingsSection title="project.settings.Label">
                            <BoardSettingsLabelList />
                        </BoardSettingsSection>
                        {(isAdmin || project.owner_uid === currentUser.uid) && (
                            <BoardSettingsSection title="common.Other">
                                <BoardSettingsOther />
                            </BoardSettingsSection>
                        )}
                    </div>
                )}
                {visited.includes("apps") && (
                    <div hidden={section !== "apps"}>
                        <BoardSettingsSection title="project.settings.Apps" wide>
                            <BoardSettingsApps key={project.uid} />
                        </BoardSettingsSection>
                    </div>
                )}
                {visited.includes("people") && (
                    <div hidden={section !== "people"} className="space-y-6">
                        <nav aria-label={t("board.teams.People")} className="flex flex-wrap gap-2">
                            {["participants", "teams"].map((key) => (
                                <Button
                                    key={key}
                                    size="sm"
                                    aria-pressed={peopleTab === key}
                                    variant={peopleTab === key ? "secondary" : "ghost"}
                                    onClick={() => {
                                        setPeopleTab(key);
                                        if (key === "teams") setTeamsVisited(true);
                                    }}
                                >
                                    {t(key === "teams" ? "board.teams.Teams" : "board.teams.Participants")}
                                </Button>
                            ))}
                        </nav>
                        <div hidden={peopleTab !== "participants"} className="space-y-6">
                            {notifications}
                            {numMembers > 0 && (
                                <BoardSettingsSection title="project.settings.Member roles">
                                    <BoardSettingsMemberRoleList key={project.uid} />
                                </BoardSettingsSection>
                            )}
                            <BoardSettingsSection title="project.settings.Email notifications">
                                <BoardSettingsEmailNotifications />
                            </BoardSettingsSection>
                        </div>
                        {teamsVisited && (
                            <div hidden={peopleTab !== "teams"}>
                                <BoardSettingsSection title="board.teams.Teams">
                                    <BoardSettingsTeams
                                        projectUID={project.uid}
                                        accountUID={currentUser.uid}
                                        active={section === "people" && peopleTab === "teams"}
                                    />
                                </BoardSettingsSection>
                            </div>
                        )}
                    </div>
                )}
                {visited.includes("automation") && (
                    <div hidden={section !== "automation"} className="space-y-6">
                        {(isAdmin || project.owner_uid === currentUser.uid) && (
                            <BoardSettingsSection title="project.settings.Execution binding">
                                <BoardSettingsExecutionBindingStatus />
                            </BoardSettingsSection>
                        )}
                        <BoardSettingsSection title="project.settings.Internal bots">
                            <BoardSettingsInternalBotList key={project.uid} />
                        </BoardSettingsSection>
                        <BoardSettingsSection title="project.settings.Chat templates">
                            <BoardSettingsChatTemplateList key={project.uid} />
                        </BoardSettingsSection>
                    </div>
                )}
            </Suspense>
        </Flex>
    );
});

export default BoardSettingsList;
