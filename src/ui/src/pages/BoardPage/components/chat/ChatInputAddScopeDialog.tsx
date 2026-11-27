import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import Tabs from "@/components/base/Tabs";
import { ProjectCard, ProjectColumn, ProjectWiki } from "@/core/models";
import { useBoardChat } from "@/core/providers/BoardChatProvider";
import { TBoardViewType, useBoardController } from "@/core/providers/BoardController";
import { TChatScope } from "@langboard/core/types";
import { Utils } from "@langboard/core/utils";
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/core/utils/ComponentUtils";

const AVAILABLE_VIEWS: TBoardViewType[] = ["board", "card", "wiki"];

export interface IChatInputAddScopeDialogProps {
    className?: string;
    showLabel?: bool;
}

function ChatInputAddScopeDialog({ className, showLabel = false }: IChatInputAddScopeDialogProps) {
    const { boardViewType } = useBoardController();
    const { isSending } = useBoardChat();
    const [t] = useTranslation();
    const [isOpened, setIsOpened] = useState(false);

    if (!AVAILABLE_VIEWS.includes(boardViewType)) {
        return null;
    }

    return (
        <Dialog.Root open={isOpened} onOpenChange={setIsOpened}>
            <Dialog.Trigger asChild>
                <Button
                    type="button"
                    variant="ghost"
                    size={showLabel ? "sm" : "icon-sm"}
                    className={cn(showLabel && "justify-start gap-2", className)}
                    title={showLabel ? undefined : t("project.Add scope")}
                    disabled={isSending}
                    titleSide="top"
                    titleAlign="start"
                >
                    <IconComponent icon="book-text" size="4" />
                    {showLabel && <span>{t("project.Add scope")}</span>}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="pt-8 sm:max-w-screen-xs md:max-w-screen-sm lg:max-w-screen-md" aria-describedby="">
                <Dialog.Title />
                <ChatInputAddScopeDialogContent setIsOpened={setIsOpened} />
            </Dialog.Content>
        </Dialog.Root>
    );
}

const BOARD_SCOPE_TABS: TChatScope[] = ["project_column", "card"];
const WIKI_SCOPE_TABS: TChatScope[] = ["project_wiki"];

function ChatInputAddScopeDialogContent({ setIsOpened }: { setIsOpened: React.Dispatch<React.SetStateAction<bool>> }) {
    const { boardViewType } = useBoardController();
    const { projectUID, selectedScope } = useBoardChat();
    const [t] = useTranslation();
    const [searchText, setSearchText] = useState("");
    const lastInputValueRef = useRef(searchText);
    const throttleSearchTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const columns = ProjectColumn.Model.useModels(
        (model) => model.project_uid === projectUID && (!searchText || model.name.includes(searchText)),
        [projectUID, searchText]
    );
    const cards = ProjectCard.Model.useModels(
        (model) =>
            model.project_uid === projectUID && (!searchText || model.title.includes(searchText) || model.project_column_name.includes(searchText)),
        [projectUID, searchText]
    );
    const wikis = ProjectWiki.Model.useModels(
        (model) => model.project_uid === projectUID && (!searchText || model.title.includes(searchText)),
        [projectUID, searchText]
    );
    const availableScopeTabs = useMemo(() => {
        switch (boardViewType) {
            case "board":
            case "card":
                return BOARD_SCOPE_TABS;
            case "wiki":
                return WIKI_SCOPE_TABS;
            default:
                return [];
        }
    }, [boardViewType]);
    const [tab, setTab] = useState<TChatScope>(selectedScope?.[0] ?? "project_column");
    const scopes = useMemo(() => {
        switch (tab) {
            case "project_column":
                return columns;
            case "card":
                return cards;
            case "project_wiki":
                return wikis;
            default:
                return [];
        }
    }, [tab, columns, cards, wikis]);
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        setSearchText(e.currentTarget.value);
        lastInputValueRef.current = e.currentTarget.value;
    };
    const handleKeyDown = () => {
        if (throttleSearchTimeoutRef.current) {
            clearTimeout(throttleSearchTimeoutRef.current);
            throttleSearchTimeoutRef.current = null;
        }
    };
    const handleClearSearchText = () => {
        setSearchText("");
        lastInputValueRef.current = "";
    };

    useEffect(() => {
        if (!selectedScope) {
            if (!availableScopeTabs.includes(tab)) {
                setTab(availableScopeTabs[0]);
            }
            return;
        }

        if (!availableScopeTabs.includes(selectedScope[0])) {
            setTab(availableScopeTabs[0]);
            return;
        }

        setTab(selectedScope[0]);
    }, [availableScopeTabs, selectedScope]);

    return (
        <Flex direction="col" gap="2" w="full">
            <Input
                placeholder={t("datatable.Search...")}
                value={searchText}
                onChange={handleInputChange}
                onKeyDown={handleKeyDown}
                leftIcon={<Search />}
                clearable
                onClear={handleClearSearchText}
                wrapperProps={{ className: "max-w-64" }}
            />
            <Tabs.Provider value={tab} onValueChange={(value) => setTab(value as TChatScope)}>
                <Tabs.List className="w-full">
                    {availableScopeTabs.map((scopeTab) => (
                        <Tabs.Trigger key={Utils.String.Token.reactKey(`chat.input.scope.tab.${scopeTab}`)} value={scopeTab}>
                            {t(`project.chatScopes.${scopeTab}`)}
                        </Tabs.Trigger>
                    ))}
                </Tabs.List>
                <Tabs.Content value={tab} className="max-h-[50vh] overflow-y-auto rounded-md border border-border">
                    {scopes.map((scope) => (
                        <ChatInputAddScopeDialogContentScopeItemProxy
                            key={Utils.String.Token.reactKey(`chat.input.scope.item.${tab}.${scope.uid}`)}
                            scopeType={tab}
                            scope={scope}
                            setIsOpened={setIsOpened}
                        />
                    ))}
                </Tabs.Content>
            </Tabs.Provider>
        </Flex>
    );
}

interface IChatInputAddScopeDialogContentScopeItemProxyProps {
    scopeType: TChatScope;
    scope: ProjectCard.TModel | ProjectColumn.TModel | ProjectWiki.TModel;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
}

function ChatInputAddScopeDialogContentScopeItemProxy({ scopeType, scope, setIsOpened }: IChatInputAddScopeDialogContentScopeItemProxyProps) {
    switch (scopeType) {
        case "project_column":
            return <ChatInputAddScopeDialogContentScopeColumnItem scope={scope as ProjectColumn.TModel} setIsOpened={setIsOpened} />;
        case "card":
            return <ChatInputAddScopeDialogContentScopeCardItem scope={scope as ProjectCard.TModel} setIsOpened={setIsOpened} />;
        case "project_wiki":
            return <ChatInputAddScopeDialogContentScopeWikiItem scope={scope as ProjectWiki.TModel} setIsOpened={setIsOpened} />;
        default:
            return null;
    }
}

function ChatInputAddScopeDialogContentScopeColumnItem({
    scope,
    setIsOpened,
}: {
    scope: ProjectColumn.TModel;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
}) {
    const name = scope.useField("name");

    return <ChatInputAddScopeDialogContentScopeItem scopeType="project_column" uid={scope.uid} title={name} setIsOpened={setIsOpened} />;
}

function ChatInputAddScopeDialogContentScopeCardItem({
    scope,
    setIsOpened,
}: {
    scope: ProjectCard.TModel;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
}) {
    const title = scope.useField("title");
    const columnName = scope.useField("project_column_name");
    const titleWithColumn = columnName ? `${title} - ${columnName}` : title;

    return <ChatInputAddScopeDialogContentScopeItem scopeType="card" uid={scope.uid} title={titleWithColumn} setIsOpened={setIsOpened} />;
}

function ChatInputAddScopeDialogContentScopeWikiItem({
    scope,
    setIsOpened,
}: {
    scope: ProjectWiki.TModel;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
}) {
    const title = scope.useField("title");

    return <ChatInputAddScopeDialogContentScopeItem scopeType="project_wiki" uid={scope.uid} title={title} setIsOpened={setIsOpened} />;
}

function ChatInputAddScopeDialogContentScopeItem({
    scopeType,
    uid,
    title,
    setIsOpened,
}: {
    scopeType: TChatScope;
    uid: string;
    title: string;
    setIsOpened: React.Dispatch<React.SetStateAction<bool>>;
}) {
    const { setSelectedScope } = useBoardChat();
    const handleClick = () => {
        setSelectedScope(() => [scopeType, uid]);
        setIsOpened(() => false);
    };

    return (
        <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-start rounded-none first:rounded-t-md last:rounded-b-md [&:not(:first-child)]:border-t"
            title={title}
            titleAlign="start"
            titleSide="bottom"
            onClick={handleClick}
        >
            <span className="w-full truncate text-left">{title}</span>
        </Button>
    );
}

export default ChatInputAddScopeDialog;
