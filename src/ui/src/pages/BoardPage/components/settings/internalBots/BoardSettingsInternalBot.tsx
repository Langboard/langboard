import Avatar from "@/components/base/Avatar";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Checkbox from "@/components/base/Checkbox";
import Collaborative from "@/components/Collaborative";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Label from "@/components/base/Label";
import Select from "@/components/base/Select";
import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useChangeProjectInternalBot from "@/controllers/api/board/settings/useChangeProjectInternalBot";
import useChangeProjectInternalBotSettings from "@/controllers/api/board/settings/useChangeProjectInternalBotSettings";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { InternalBotModel, Project } from "@/core/models";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { CheckedState } from "@radix-ui/react-checkbox";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface IBoardSettingsInternalBotProps {
    botType: InternalBotModel.EInternalBotType;
}

const BoardSettingsInternalBot = memo(({ botType }: IBoardSettingsInternalBotProps) => {
    const [t] = useTranslation();
    const { project } = useBoardSettings();
    const internalBots = InternalBotModel.Model.useModels((model) => model.bot_type === botType);
    const projectInternalBots = project.useForeignFieldArray("internal_bots");
    const currentInternalBot = useMemo(
        () => projectInternalBots.find((model) => model.bot_type === botType) ?? internalBots[0],
        [projectInternalBots, botType]
    );
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useChangeProjectInternalBot(project.uid);

    const handleValueChange = (value: string) => {
        if (isValidating || value === currentInternalBot.uid) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            internal_bot_uid: value,
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
                return t("successes.Internal bot changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Box>
            <Box weight="semibold" textSize="base">
                {t(`internalBot.botTypes.${botType}`)}
            </Box>
            <Flex items="center" justify="between" gap="3">
                <Select.Root value={currentInternalBot.uid} onValueChange={handleValueChange}>
                    <Select.Trigger>
                        <Select.Value />
                    </Select.Trigger>
                    <Select.Content>
                        {internalBots.map((bot) => (
                            <Select.Item key={`board-settings-internal-bot-select-${bot.uid}`} value={bot.uid}>
                                <BoardSettingsInternalBotItem internalBot={bot} />
                            </Select.Item>
                        ))}
                    </Select.Content>
                </Select.Root>
                <BoardSettingsInternalBotSettings botType={botType} />
            </Flex>
        </Box>
    );
});

interface IBoardSettingsInternalBotItemProps {
    internalBot: InternalBotModel.TModel;
}

const BoardSettingsInternalBotItem = memo(({ internalBot }: IBoardSettingsInternalBotItemProps) => {
    const displayName = internalBot.useField("display_name");
    const avatar = internalBot.useField("avatar");

    return (
        <Flex items="center" gap="2">
            <Avatar.Root size="xs">
                <Avatar.Image src={avatar} />
                <Avatar.Fallback>
                    <IconComponent icon="bot" className="size-[80%]" />
                </Avatar.Fallback>
            </Avatar.Root>
            <span className="truncate">{displayName}</span>
        </Flex>
    );
});

interface IBoardSettingsInternalBotSettingsProps {
    botType: InternalBotModel.EInternalBotType;
}

const BoardSettingsInternalBotSettings = memo(({ botType }: IBoardSettingsInternalBotSettingsProps) => {
    const [t] = useTranslation();
    const { project } = useBoardSettings();
    const internalBotSettings = project.useField("internal_bot_settings");
    const settings = useMemo(() => internalBotSettings?.[botType] || { prompt: "", use_default_prompt: true }, [internalBotSettings, botType]);
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useChangeProjectInternalBotSettings(project.uid);

    const handleChangeDefault = (checked: CheckedState) => {
        if (Utils.Type.isString(checked)) {
            return;
        }

        if (isValidating || checked === settings.use_default_prompt) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            bot_type: botType,
            use_default_prompt: checked,
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
                return t("successes.Internal bot settings changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <Flex items="center" gap="1.5">
            <BoardSettingsInternalBotSettingsPromptDialog botType={botType} settings={settings} />
            <Label display="flex" items="center" gap="1.5" w="24" cursor="pointer">
                <Checkbox checked={settings.use_default_prompt} onCheckedChange={handleChangeDefault} />
                {t("common.Use default")}
            </Label>
        </Flex>
    );
});

interface IBoardSettingsInternalBotSettingsPromptDialogProps {
    botType: InternalBotModel.EInternalBotType;
    settings: Project.TModel["internal_bot_settings"][InternalBotModel.EInternalBotType];
}

function BoardSettingsInternalBotSettingsPromptDialog({ botType, settings }: IBoardSettingsInternalBotSettingsPromptDialogProps) {
    const [t] = useTranslation();
    const { project } = useBoardSettings();
    const [isOpened, setIsOpened] = useState(false);
    const promptInputRef = useRef<HTMLTextAreaElement>(null);
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync } = useChangeProjectInternalBotSettings(project.uid);

    const save = () => {
        if (isValidating || !promptInputRef.current) {
            return;
        }

        setIsValidating(true);

        const promise = mutateAsync({
            bot_type: botType,
            prompt: promptInputRef.current.value,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Saving..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Internal bot settings changed successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    useEffect(() => {
        if (isOpened && settings.use_default_prompt) {
            Toast.Add.error(t("project.Cannot edit prompt when using default prompt."));
            setIsOpened(false);
        }
    }, [isOpened, settings]);

    return (
        <Dialog.Root open={isOpened} onOpenChange={setIsOpened}>
            <Dialog.Trigger asChild>
                <Button type="button" size="sm" disabled={settings.use_default_prompt}>
                    {t("project.Prompt")}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="sm:max-w-md" aria-describedby="">
                <Dialog.Header>
                    <Dialog.Title>{t("project.Edit prompt")}</Dialog.Title>
                </Dialog.Header>
                <Box mt="4">
                    <Collaborative.Textarea
                        collaborationType={EEditorCollaborationType.BoardSettings}
                        uid={project.uid}
                        section={`internal-bot-prompt-${botType}`}
                        field="prompt"
                        defaultValue={settings.prompt}
                        resetSyncedValueToDefault
                        autoFocus
                        resize="none"
                        className="h-36"
                        autoComplete="off"
                        disabled={isValidating}
                        placeholder={t("project.Prompt")}
                        ref={promptInputRef}
                    />
                </Box>
                <Dialog.Footer className="mt-6 flex-col gap-2 sm:justify-end sm:gap-0">
                    <Dialog.Close asChild>
                        <Button type="button" variant="secondary" disabled={isValidating}>
                            {t("common.Cancel")}
                        </Button>
                    </Dialog.Close>
                    <SubmitButton type="button" isValidating={isValidating} onClick={save}>
                        {t("common.Save")}
                    </SubmitButton>
                </Dialog.Footer>
            </Dialog.Content>
        </Dialog.Root>
    );
}

export default BoardSettingsInternalBot;
