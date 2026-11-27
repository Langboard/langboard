import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Collapsible from "@/components/base/Collapsible";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import ScrollArea from "@/components/base/ScrollArea";
import Textarea from "@/components/base/Textarea";
import useGetProjectChatTemplates from "@/controllers/api/board/chat/useGetProjectChatTemplates";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ChatTemplateModel } from "@/core/models";
import { useBoardChat } from "@/core/providers/BoardChatProvider";
import { useCallback, useEffect, useReducer, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/core/utils/ComponentUtils";

export interface IChatTemplateListDialogProps {
    className?: string;
    chatInputRef: React.RefObject<HTMLTextAreaElement | null>;
    showLabel?: bool;
    updateHeight: () => void;
}

function ChatTemplateListDialog({ className, chatInputRef, showLabel = false, updateHeight }: IChatTemplateListDialogProps) {
    const [t] = useTranslation();
    const [isOpened, setIsOpened] = useState(false);
    const selectTemplate = useCallback(
        (template: string) => {
            if (chatInputRef.current) {
                chatInputRef.current.value = template;
            }
            updateHeight();
            setIsOpened(false);
            setTimeout(() => {
                if (chatInputRef.current) {
                    chatInputRef.current.focus();
                }
            }, 0);
        },
        [updateHeight, setIsOpened]
    );
    const handleClick = useCallback(() => {
        if (chatInputRef.current) {
            chatInputRef.current.focus();
        }

        setIsOpened((prev) => !prev);
    }, [setIsOpened]);

    return (
        <Dialog.Root open={isOpened} onOpenChange={setIsOpened}>
            <Button
                type="button"
                variant="ghost"
                size={showLabel ? "sm" : "icon-sm"}
                className={cn(showLabel && "justify-start gap-2", className)}
                title={showLabel ? undefined : t("project.settings.Chat templates")}
                titleSide="top"
                titleAlign="start"
                onClick={handleClick}
            >
                <IconComponent icon="letter-text" size="4" />
                {showLabel && <span>{t("project.settings.Chat templates")}</span>}
            </Button>
            <Dialog.Content className="h-full max-h-[70vh] px-0 pb-2 pt-8">
                <Dialog.Title hidden />
                <Dialog.Description hidden />
                <ChatTemplateList selectTemplate={selectTemplate} />
            </Dialog.Content>
        </Dialog.Root>
    );
}

interface IChatTemplateListProps {
    selectTemplate: (template: string) => void;
}

function ChatTemplateList(props: IChatTemplateListProps) {
    const { projectUID } = useBoardChat();
    const chatTemplates = ChatTemplateModel.Model.useModels((model) => model.filterable_table === "project" && model.filterable_uid === projectUID);
    const { mutate } = useGetProjectChatTemplates();
    const [updated, updateScroll] = useReducer((x) => x + 1, 0);

    useEffect(() => {
        mutate(
            { project_uid: projectUID },
            {
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({});

                    handle(error);
                },
            }
        );
    }, []);

    return (
        <ScrollArea.Root mutable={updated}>
            <Flex direction="col" w="full" h="full" className="max-h-[calc(70vh_-_theme(spacing.11))] border-t">
                {chatTemplates.map((chatTemplate) => (
                    <ChatTemplate key={`chat-template-list-${chatTemplate.uid}`} chatTemplate={chatTemplate} updateScroll={updateScroll} {...props} />
                ))}
            </Flex>
        </ScrollArea.Root>
    );
}

interface ChatTemplateProps extends IChatTemplateListProps {
    chatTemplate: ChatTemplateModel.TModel;
    updateScroll: () => void;
}

function ChatTemplate({ selectTemplate, chatTemplate, updateScroll }: ChatTemplateProps) {
    const [t] = useTranslation();
    const name = chatTemplate.useField("name");
    const template = chatTemplate.useField("template");
    const [isOpened, setIsOpened] = useState(false);
    const handleSelect = useCallback(() => {
        selectTemplate(template);
    }, [selectTemplate]);

    useEffect(() => {
        setTimeout(() => {
            updateScroll();
        }, 350);
    }, [isOpened]);

    return (
        <Collapsible.Root className="border-b" open={isOpened} onOpenChange={setIsOpened}>
            <Collapsible.Trigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    className="w-full justify-between truncate rounded-none [&[data-state=open]>:last-child]:rotate-180"
                >
                    {name}
                    <IconComponent icon="chevron-down" size="4" className="transition-all" />
                </Button>
            </Collapsible.Trigger>
            <Collapsible.Content className="overflow-hidden p-2 pb-0 data-[state=closed]:animate-collapse-up data-[state=open]:animate-collapse-down">
                <Textarea readOnly value={template} resize="none" className="h-32 focus-visible:ring-0" />
            </Collapsible.Content>
            <Box p="2" className="text-right">
                <Button type="button" size="sm" onClick={handleSelect}>
                    {t("common.Use this template")}
                </Button>
            </Box>
        </Collapsible.Root>
    );
}

export default ChatTemplateListDialog;
