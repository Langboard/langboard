"use client";

import * as React from "react";
import { ListStyleType, someList, toggleList } from "@platejs/list";
import { List, ListOrdered, ListTodoIcon } from "lucide-react";
import { ToolbarButton, ToolbarSplitButton, ToolbarSplitButtonPrimary, ToolbarSplitButtonSecondary } from "@/components/plate-ui/toolbar";
import { useTranslation } from "react-i18next";
import { useEditorRef, useEditorSelector } from "platejs/react";
import DropdownMenu from "@/components/base/DropdownMenu";
import { useIndentTodoToolBarButton, useIndentTodoToolBarButtonState } from "@platejs/list/react";

const INDENT_LIST = [
    {
        type: ListStyleType.Disc,
        icon: <List />,
        label: "editor.Bulleted list",
    },
    {
        type: ListStyleType.Decimal,
        icon: <ListOrdered />,
        label: "editor.Numbered list",
    },
];

export function IndentListToolbarButton() {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const [open, setOpen] = React.useState(false);
    const pressed = useEditorSelector(
        (editor) =>
            someList(
                editor,
                Object.values(INDENT_LIST).map((indent) => indent.type)
            ),
        []
    );

    return (
        <ToolbarSplitButton pressed={open}>
            <ToolbarSplitButtonPrimary
                className="data-[state=on]:bg-accent data-[state=on]:text-accent-foreground"
                onClick={() => {
                    toggleList(editor, {
                        listStyleType: ListStyleType.Disc,
                    });
                }}
                data-state={pressed ? "on" : "off"}
            >
                <List className="size-4" />
            </ToolbarSplitButtonPrimary>
            <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false}>
                <DropdownMenu.Trigger asChild>
                    <ToolbarSplitButtonSecondary />
                </DropdownMenu.Trigger>
                <DropdownMenu.Content align="start" alignOffset={-32}>
                    <DropdownMenu.Group>
                        {INDENT_LIST.map((item) => (
                            <DropdownMenu.Item
                                onClick={() =>
                                    toggleList(editor, {
                                        listStyleType: ListStyleType.Disc,
                                    })
                                }
                            >
                                <div className="flex items-center gap-2">
                                    {item.icon}
                                    {t(item.label)}
                                </div>
                            </DropdownMenu.Item>
                        ))}
                    </DropdownMenu.Group>
                </DropdownMenu.Content>
            </DropdownMenu.Root>
        </ToolbarSplitButton>
    );
}

export function TodoListToolbarButton(props: React.ComponentProps<typeof ToolbarButton>) {
    const [t] = useTranslation();
    const state = useIndentTodoToolBarButtonState({ nodeType: "todo" });
    const { props: buttonProps } = useIndentTodoToolBarButton(state);

    return (
        <ToolbarButton {...props} {...buttonProps} tooltip={t("editor.Todo")}>
            <ListTodoIcon />
        </ToolbarButton>
    );
}
