import Button from "@/components/base/Button";
import DateTimePicker from "@/components/base/DateTimePicker";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import CollaborativeControlOverlay from "@/components/Collaborative/ControlOverlay";
import { useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
import type { ICollaborativeTextMeta } from "@/components/Collaborative/useCollaborativeText";
import MoreMenu from "@/components/MoreMenu";
import useChangeCardCheckitemDeadline from "@/controllers/api/card/checkitem/useChangeCardCheckitemDeadline";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { cn } from "@/core/utils/ComponentUtils";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface ICheckitemDeadlineMeta {
    updatedAt: number;
}

const normalizeDeadline = (deadline: Date | undefined) => {
    if (!deadline) {
        return undefined;
    }

    const nextDeadline = new Date(deadline);
    nextDeadline.setSeconds(0, 0);
    return nextDeadline;
};

const getDeadlineTime = (deadline: Date | undefined) => normalizeDeadline(deadline)?.getTime() ?? null;

const serializeDeadline = (deadline: Date | undefined) => normalizeDeadline(deadline)?.toISOString() ?? "";

const parseDeadline = (value: string) => {
    if (!value) {
        return undefined;
    }

    const deadline = new Date(value);
    if (Number.isNaN(deadline.getTime())) {
        return undefined;
    }

    return normalizeDeadline(deadline);
};

function BoardCardCheckitemMoreMenuDeadline(): React.JSX.Element {
    const { projectUID, card, sharedClassNames } = useBoardCard();
    const { model: checkitem } = ModelRegistry.ProjectCheckitem.useContext();
    const [t] = useTranslation();
    const { mutateAsync: changeCheckitemDeadlineMutateAsync } = useChangeCardCheckitemDeadline({ interceptToast: true });
    const deadline = checkitem.useField("deadline_at");
    const [draftDeadline, setDraftDeadline] = useState<Date | undefined>(deadline ?? undefined);
    const [isOpened, setIsOpened] = useState(false);
    const { isSynced, remoteMeta, updateMeta, updateValue } = useCollaborativeText({
        defaultValue: serializeDeadline(deadline ?? undefined),
        disabled: !isOpened,
        collaborationType: EEditorCollaborationType.Card,
        uid: card.uid,
        section: `checkitem-${checkitem.uid}-deadline`,
        field: "value",
        resetSyncedValueToDefault: true,
        onValueChange: (value) => {
            setDraftDeadline(parseDeadline(value));
        },
    });
    const isWaitingForSync = isOpened && !isSynced;
    const latestRemoteMeta = remoteMeta.reduce<ICollaborativeTextMeta<ICheckitemDeadlineMeta> | null>((acc, meta) => {
        const value = meta.value;
        if (!value || !Utils.Type.isObject(value) || !Utils.Type.isNumber((value as Record<string, unknown>).updatedAt)) {
            return acc;
        }

        const parsedMeta = meta as ICollaborativeTextMeta<ICheckitemDeadlineMeta>;
        if (!acc || acc.value.updatedAt < parsedMeta.value.updatedAt) {
            return parsedMeta;
        }

        return acc;
    }, null);

    const changeDeadline = (endCallback: (shouldClose: bool) => void) => {
        if (isWaitingForSync) {
            return;
        }

        const nextDeadline = normalizeDeadline(draftDeadline);

        if (getDeadlineTime(nextDeadline) === getDeadlineTime(deadline)) {
            updateMeta(null);
            endCallback(true);
            return;
        }

        const promise = changeCheckitemDeadlineMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            checkitem_uid: checkitem.uid,
            deadline_at: nextDeadline || "",
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Checkitem deadline changed successfully.");
            },
            finally: () => {
                updateMeta(null);
                endCallback(true);
            },
        });
    };

    const resetDraftDeadline = (opened: bool) => {
        setIsOpened(opened);
        if (opened) {
            setDraftDeadline(deadline ?? undefined);
            return;
        }

        updateMeta(null);
    };

    const clearDraftDeadline = () => {
        if (isWaitingForSync) {
            return;
        }

        updateMeta({ updatedAt: Date.now() } satisfies ICheckitemDeadlineMeta);
        updateValue("");
    };

    const updateDraftDeadline = (deadline: Date | undefined) => {
        if (isWaitingForSync) {
            return;
        }

        const nextDeadline = normalizeDeadline(deadline);
        updateMeta({ updatedAt: Date.now() } satisfies ICheckitemDeadlineMeta);
        updateValue(serializeDeadline(nextDeadline));
    };

    return (
        <MoreMenu.PopoverItem
            modal
            menuName={t("card.Set deadline")}
            contentProps={{ className: sharedClassNames.popoverContent }}
            onOpenChange={resetDraftDeadline}
            onSave={changeDeadline}
        >
            <Flex items="center" position="relative">
                <DateTimePicker
                    value={draftDeadline}
                    disabled={isWaitingForSync}
                    min={new Date(new Date().setMinutes(new Date().getMinutes() + 30))}
                    onChange={updateDraftDeadline}
                    timePicker={{
                        hour: true,
                        minute: true,
                        second: false,
                    }}
                    renderTrigger={() => (
                        <Button
                            type="button"
                            variant={draftDeadline ? "default" : "outline"}
                            className={cn("h-8 gap-2 px-3 lg:h-10", draftDeadline && "rounded-r-none")}
                            title={t("card.Set deadline")}
                            disabled={isWaitingForSync}
                        >
                            <IconComponent icon="calendar" size="4" />
                            {isWaitingForSync
                                ? t("common.Syncing draft...")
                                : draftDeadline
                                  ? Utils.String.formatDateLocale(draftDeadline)
                                  : t("card.Set deadline")}
                        </Button>
                    )}
                />
                {draftDeadline && (
                    <Button
                        type="button"
                        variant="default"
                        className="h-8 gap-2 rounded-l-none border-l border-l-secondary/70 px-2 lg:h-10"
                        onClick={clearDraftDeadline}
                        disabled={isWaitingForSync}
                    >
                        <IconComponent icon="trash-2" size="4" />
                    </Button>
                )}
                {latestRemoteMeta && (
                    <CollaborativeControlOverlay
                        color={latestRemoteMeta.color}
                        name={latestRemoteMeta.name}
                        labelClassName="absolute -right-1 -top-4 z-[9999]"
                    />
                )}
            </Flex>
        </MoreMenu.PopoverItem>
    );
}

export default BoardCardCheckitemMoreMenuDeadline;
