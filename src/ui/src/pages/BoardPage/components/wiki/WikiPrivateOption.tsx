import Flex from "@/components/base/Flex";
import Label from "@/components/base/Label";
import Skeleton from "@/components/base/Skeleton";
import Switch from "@/components/base/Switch";
import Toast from "@/components/base/Toast";
import CollaborativeControlOverlay from "@/components/Collaborative/ControlOverlay";
import { useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import { SkeletonUserAvatarList } from "@/components/UserAvatarList";
import useChangeWikiPublic from "@/controllers/api/wiki/useChangeWikiPublic";
import useUpdateWikiAssignees from "@/controllers/api/wiki/useUpdateWikiAssignees";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ProjectWiki, User } from "@/core/models";
import { useBoardWiki } from "@/core/providers/BoardWikiProvider";
import { cn } from "@/core/utils/ComponentUtils";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ROUTES } from "@/core/routing/constants";
import MultiSelectAssignee, { IFormProps, TSaveHandler } from "@/components/MultiSelectAssignee";
import { TUserLikeModel } from "@/core/models/ModelRegistry";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { getUserUIDs, isAssignableWikiMember, parseCollaborativeMemberUIDs } from "@/core/utils/CollaborativeSelectionUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { EHttpStatus } from "@langboard/core/enums";
import { Utils } from "@langboard/core/utils";

export interface IWikiPrivateOptionProps {
    wiki: ProjectWiki.TModel;
    changeTab: (uid: string) => void;
}

export function SkeletonWikiPrivateOption() {
    return (
        <Flex items="center" gap="4" pl="1" h="8" justify={{ initial: "between", sm: "start" }}>
            <Flex inline items="center" gap="2">
                <Skeleton h="6" w="11" rounded="full" />
                <Skeleton h="6" w="20" />
            </Flex>
            <SkeletonUserAvatarList count={4} size="sm" spacing="none" />
        </Flex>
    );
}

interface IMemberSelectionMeta {
    added: bool;
    memberUID: string;
    updatedAt: number;
}

interface IRemoteMemberMetaState {
    actorName: string;
    borderColor: string;
    updatedAt: number;
}

const WikiPrivateOption = memo(({ wiki, changeTab }: IWikiPrivateOptionProps) => {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { project, projectMembers, currentUser } = useBoardWiki();
    const isPublic = wiki.useField("is_public");
    const forbidden = wiki.useField("forbidden");
    const isChangedTabRef = useRef(false);
    const assignedMembers = wiki.useForeignFieldArray("assigned_members");
    const groups = currentUser.useForeignFieldArray("user_groups");
    const allItems = useMemo(() => projectMembers.filter((item) => isAssignableWikiMember(item, currentUser.uid)), [currentUser, projectMembers]);
    const originalAssignees = useMemo(
        () => assignedMembers.filter((item) => isAssignableWikiMember(item, currentUser.uid)),
        [assignedMembers, currentUser]
    );
    const showableAssignees = useMemo(() => assignedMembers.filter((member) => member.isValidUser()), [assignedMembers]);
    const assignedMemberUIDs = useMemo(() => assignedMembers.map((member) => member.uid), [assignedMembers]);
    const defaultSelectedMemberUIDs = useMemo(() => JSON.stringify(assignedMemberUIDs), [assignedMemberUIDs]);
    const [isAssigneePopoverOpen, setIsAssigneePopoverOpen] = useState(false);
    const { isSynced, remoteMeta, updateMeta, updateValue, value } = useCollaborativeText({
        defaultValue: defaultSelectedMemberUIDs,
        disabled: isPublic || !isAssigneePopoverOpen,
        collaborationType: EEditorCollaborationType.Wiki,
        uid: wiki.uid,
        section: "private-assignees",
        field: "selected-member-uids",
        resetSyncedValueToDefault: true,
    });
    const isWaitingForAssigneeSync = !isPublic && isAssigneePopoverOpen && !isSynced;
    const selectedMemberUIDs = parseCollaborativeMemberUIDs(value, assignedMemberUIDs);
    const collaborativeAssignees = useMemo(
        () =>
            selectedMemberUIDs
                .map((uid) => projectMembers.find((member) => member.uid === uid))
                .filter((member): member is User.TModel => !!member && !member.is_admin),
        [projectMembers, selectedMemberUIDs]
    );
    const visibleCollaborativeAssignees = useMemo(
        () => collaborativeAssignees.filter((item) => item.uid !== currentUser.uid),
        [collaborativeAssignees, currentUser]
    );
    const remoteMemberMetaMap = useMemo(() => {
        return remoteMeta.reduce<Record<string, IRemoteMemberMetaState & { added: bool }>>((acc, meta) => {
            const value = meta.value;
            if (
                !value ||
                !Utils.Type.isObject(value) ||
                !Utils.Type.isString((value as Record<string, unknown>).memberUID) ||
                !Utils.Type.isBool((value as Record<string, unknown>).added) ||
                !Utils.Type.isNumber((value as Record<string, unknown>).updatedAt)
            ) {
                return acc;
            }

            const parsedValue = value as IMemberSelectionMeta;
            const previous = acc[parsedValue.memberUID];
            if (!previous || previous.updatedAt < parsedValue.updatedAt) {
                acc[parsedValue.memberUID] = {
                    added: parsedValue.added,
                    actorName: meta.name,
                    borderColor: meta.color,
                    updatedAt: parsedValue.updatedAt,
                };
            }

            return acc;
        }, {});
    }, [remoteMeta]);
    const pendingSelectedMemberUIDsRef = useRef<string[] | null>(null);
    const flushSelectedMemberUIDsTimeoutRef = useRef<number | null>(null);
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync: changeWikiPublicMutateAsync } = useChangeWikiPublic({ interceptToast: true });
    const { mutateAsync: updateWikiAssigneesMutateAsync } = useUpdateWikiAssignees({ interceptToast: true });

    useEffect(() => {
        return () => {
            if (flushSelectedMemberUIDsTimeoutRef.current) {
                clearTimeout(flushSelectedMemberUIDsTimeoutRef.current);
            }
        };
    }, []);

    useEffect(() => {
        if (forbidden && !isChangedTabRef.current) {
            Toast.Add.error(t("errors.requests.PE2005"));
            changeTab("");
            isChangedTabRef.current = true;
        } else {
            isChangedTabRef.current = false;
        }
    }, [forbidden]);

    const savePrivateState = (privateState: bool) => {
        if (isValidating || privateState === !isPublic) {
            return;
        }

        setIsValidating(true);

        const promise = changeWikiPublicMutateAsync({
            project_uid: project.uid,
            wiki_uid: wiki.uid,
            is_public: !privateState,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler(
                    {
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.BOARD.WIKI(project.uid)),
                        },
                    },
                    messageRef
                );

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Public state changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const saveAssignees = (items: TUserLikeModel[]) => {
        if (isValidating || isPublic || isWaitingForAssigneeSync) {
            return;
        }

        setIsValidating(true);

        const newAssignees = items.filter((item) => !("is_admin" in item && item.is_admin)).map((item) => item.uid);
        if (wiki.assigned_members.some((member) => member.uid === currentUser.uid)) {
            newAssignees.unshift(currentUser.uid);
        }

        const promise = updateWikiAssigneesMutateAsync({
            project_uid: project.uid,
            wiki_uid: wiki.uid,
            assignees: newAssignees,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Updating..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler(
                    {
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.BOARD.WIKI(project.uid)),
                        },
                    },
                    messageRef
                );

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Assigned bots and members updated successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    const handleAssigneePopoverOpenChange = useCallback(
        (open: bool) => {
            setIsAssigneePopoverOpen(open);
            if (!open) {
                pendingSelectedMemberUIDsRef.current = null;
                if (flushSelectedMemberUIDsTimeoutRef.current) {
                    clearTimeout(flushSelectedMemberUIDsTimeoutRef.current);
                    flushSelectedMemberUIDsTimeoutRef.current = null;
                }
                updateMeta(null);
            }
        },
        [updateMeta]
    );

    const flushSelectedMemberUIDs = useCallback(
        (nextSelectedMemberUIDs: string[]) => {
            pendingSelectedMemberUIDsRef.current = nextSelectedMemberUIDs;
            if (flushSelectedMemberUIDsTimeoutRef.current) {
                clearTimeout(flushSelectedMemberUIDsTimeoutRef.current);
            }

            flushSelectedMemberUIDsTimeoutRef.current = window.setTimeout(() => {
                const latestSelectedMemberUIDs = pendingSelectedMemberUIDsRef.current;
                pendingSelectedMemberUIDsRef.current = null;
                flushSelectedMemberUIDsTimeoutRef.current = null;
                if (!latestSelectedMemberUIDs) {
                    return;
                }

                updateValue(JSON.stringify(latestSelectedMemberUIDs));
            }, 0);
        },
        [updateValue]
    );

    const handleAssigneesChange = useCallback(
        (items: (string | TUserLikeModel)[]) => {
            if (isWaitingForAssigneeSync) {
                return;
            }

            const nextSelectedMemberUIDs = getUserUIDs(items);
            if (wiki.assigned_members.some((member) => member.uid === currentUser.uid) && !nextSelectedMemberUIDs.includes(currentUser.uid)) {
                nextSelectedMemberUIDs.unshift(currentUser.uid);
            }

            const addedMemberUID = nextSelectedMemberUIDs.find((uid) => !selectedMemberUIDs.includes(uid)) ?? "";
            const removedMemberUID = selectedMemberUIDs.find((uid) => !nextSelectedMemberUIDs.includes(uid)) ?? "";

            updateMeta(
                addedMemberUID || removedMemberUID
                    ? ({
                          added: !!addedMemberUID,
                          memberUID: addedMemberUID || removedMemberUID,
                          updatedAt: Date.now(),
                      } satisfies IMemberSelectionMeta)
                    : null
            );
            flushSelectedMemberUIDs(nextSelectedMemberUIDs);
        },
        [currentUser, flushSelectedMemberUIDs, isWaitingForAssigneeSync, selectedMemberUIDs, updateMeta, wiki]
    );

    return (
        <Flex items="center" gap="4" h="8">
            <Label display="inline-flex" cursor="pointer" items="center" gap="2">
                <Switch checked={!isPublic} onCheckedChange={savePrivateState} />
                <span>{t(`wiki.${isPublic ? "Public" : "Private"}`)}</span>
            </Label>
            {!isPublic && (
                <MultiSelectAssignee.Popover
                    onOpenChange={handleAssigneePopoverOpenChange}
                    onSelectedAssigneesChange={handleAssigneesChange}
                    popoverButtonProps={{
                        size: "icon",
                        className: "size-8",
                        title: t("project.Assign members"),
                    }}
                    helperContent={
                        isWaitingForAssigneeSync ? <div className="text-xs text-muted-foreground">{t("common.Syncing draft...")}</div> : null
                    }
                    popoverContentProps={{
                        className: cn(
                            "max-w-[calc(100vw_-_theme(spacing.20))]",
                            "sm:max-w-[calc(theme(screens.sm)_-_theme(spacing.60))]",
                            "lg:max-w-[calc(theme(screens.md)_-_theme(spacing.60))]",
                            "min-w-[min(theme(spacing.20),100%)]"
                        ),
                        align: "start",
                    }}
                    userAvatarListProps={{
                        maxVisible: 4,
                        size: "sm",
                        spacing: "3",
                        listAlign: "start",
                    }}
                    placeholder={t("wiki.Select members and bots...")}
                    allSelectables={allItems}
                    selectedAssignees={visibleCollaborativeAssignees}
                    originalAssignees={originalAssignees}
                    showableAssignees={showableAssignees}
                    tagContentProps={{
                        scope: {
                            projectUID: project.uid,
                            wikiUID: wiki.uid,
                        },
                    }}
                    addIconSize="6"
                    save={saveAssignees as TSaveHandler}
                    createSearchKeywords={((item: User.TModel) => [item.email, item.firstname, item.lastname]) as IFormProps["createSearchKeywords"]}
                    createLabel={(item) => {
                        item = item as User.TModel;
                        return `${item.firstname} ${item.lastname}`.trim();
                    }}
                    decorateSelectItem={
                        ((item: User.TModel) => {
                            const remoteMemberMeta = remoteMemberMetaMap[item.uid];
                            if (!remoteMemberMeta || !remoteMemberMeta.added) {
                                return {};
                            }

                            return {
                                badgeActorName: remoteMemberMeta.actorName,
                                badgeBorderColor: remoteMemberMeta.borderColor,
                            };
                        }) as IFormProps["decorateSelectItem"]
                    }
                    renderSelectableItem={
                        ((item: User.TModel) => {
                            const remoteMemberMeta = remoteMemberMetaMap[item.uid];
                            const label = `${item.firstname} ${item.lastname}`.trim();

                            if (!remoteMemberMeta || remoteMemberMeta.added) {
                                return label;
                            }

                            return (
                                <div className="relative w-full px-2 py-1">
                                    <CollaborativeControlOverlay
                                        color={remoteMemberMeta.borderColor}
                                        labelClassName="absolute left-2 top-0 z-[9999] -translate-y-1/2"
                                        name={remoteMemberMeta.actorName}
                                    />
                                    <span>{label}</span>
                                </div>
                            );
                        }) as IFormProps["renderSelectableItem"]
                    }
                    withUserGroups
                    groups={groups}
                    filterGroupUser={(item: User.TModel) =>
                        item.isValidUser() && !item.is_admin && projectMembers.some((member) => member.uid === item.uid)
                    }
                    canEdit
                />
            )}
        </Flex>
    );
});

export default WikiPrivateOption;
