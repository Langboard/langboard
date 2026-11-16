"use client";

import * as React from "react";
import type { DropdownMenuProps } from "@radix-ui/react-dropdown-menu";
import type { TElement } from "platejs";
import { DropdownMenuItemIndicator } from "@radix-ui/react-dropdown-menu";
import {
    CheckIcon,
    FileCodeIcon,
    Heading1Icon,
    Heading2Icon,
    Heading3Icon,
    ListIcon,
    ListOrderedIcon,
    PilcrowIcon,
    QuoteIcon,
    SquareIcon,
} from "lucide-react";
import { KEYS } from "platejs";
import { useEditorRef, useSelectionFragmentProp } from "platejs/react";
import DropdownMenu from "@/components/base/DropdownMenu";
import { getBlockType, setBlockType } from "@/components/Editor/transforms";
import { ToolbarButton, ToolbarMenuGroup } from "@/components/plate-ui/toolbar";
import { useTranslation } from "react-i18next";

const turnIntoItems = [
    {
        icon: <PilcrowIcon className="size-4" />,
        keywords: ["paragraph"],
        label: "editor.Text",
        value: KEYS.p,
    },
    {
        icon: <Heading1Icon className="size-4" />,
        keywords: ["title", "h1"],
        label: "editor.Heading 1",
        value: "h1",
    },
    {
        icon: <Heading2Icon className="size-4" />,
        keywords: ["subtitle", "h2"],
        label: "editor.Heading 2",
        value: "h2",
    },
    {
        icon: <Heading3Icon className="size-4" />,
        keywords: ["subtitle", "h3"],
        label: "editor.Heading 3",
        value: "h3",
    },
    {
        icon: <ListIcon className="size-4" />,
        keywords: ["unordered", "ul", "-"],
        label: "editor.Bulleted list",
        value: KEYS.ul,
    },
    {
        icon: <ListOrderedIcon className="size-4" />,
        keywords: ["ordered", "ol", "1"],
        label: "editor.Numbered list",
        value: KEYS.ol,
    },
    {
        icon: <SquareIcon className="size-4" />,
        keywords: ["checklist", "task", "checkbox", "[]"],
        label: "editor.To-do list",
        value: KEYS.listTodo,
    },
    {
        icon: <FileCodeIcon className="size-4" />,
        keywords: ["```"],
        label: "editor.Code",
        value: KEYS.codeBlock,
    },
    {
        icon: <QuoteIcon className="size-4" />,
        keywords: ["citation", "blockquote", ">"],
        label: "editor.Quote",
        value: KEYS.blockquote,
    },
];

export function TurnIntoToolbarButton(props: DropdownMenuProps) {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const [open, setOpen] = React.useState(false);

    const value = useSelectionFragmentProp({
        defaultValue: KEYS.p,
        getProp: (node) => getBlockType(node as TElement),
    });
    const selectedItem = React.useMemo(() => turnIntoItems.find((item) => item.value === (value ?? KEYS.p)) ?? turnIntoItems[0], [value]);

    return (
        <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false} {...props}>
            <DropdownMenu.Trigger asChild>
                <ToolbarButton className="min-w-[125px]" pressed={open} tooltip={t("editor.Turn into")} aria-label={t(selectedItem.label)} isDropdown>
                    {t(selectedItem.label)}
                </ToolbarButton>
            </DropdownMenu.Trigger>

            <DropdownMenu.Content
                className="ignore-click-outside/toolbar min-w-0"
                onCloseAutoFocus={(e) => {
                    e.preventDefault();
                    editor.tf.focus();
                }}
                align="start"
            >
                <ToolbarMenuGroup
                    value={value}
                    onValueChange={(type) => {
                        setBlockType(editor, type);
                        editor.tf.focus();
                    }}
                    label={t("editor.Turn into")}
                >
                    {turnIntoItems.map(({ icon, label, value: itemValue }) => (
                        <DropdownMenu.RadioItem key={itemValue} className="*:first:[span]:hidden min-w-[180px] pl-2" value={itemValue}>
                            <span className="pointer-events-none absolute right-2 flex size-3.5 items-center justify-center">
                                <DropdownMenuItemIndicator>
                                    <CheckIcon className="size-4" />
                                </DropdownMenuItemIndicator>
                            </span>
                            {icon}
                            {t(label)}
                        </DropdownMenu.RadioItem>
                    ))}
                </ToolbarMenuGroup>
            </DropdownMenu.Content>
        </DropdownMenu.Root>
    );
}
