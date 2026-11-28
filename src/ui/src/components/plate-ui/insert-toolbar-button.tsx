"use client";

import * as React from "react";
import type { DropdownMenuProps } from "@radix-ui/react-dropdown-menu";
import {
    CalendarIcon,
    FileCodeIcon,
    Heading1Icon,
    Heading2Icon,
    Heading3Icon,
    LightbulbIcon,
    ListIcon,
    ListOrderedIcon,
    MinusIcon,
    PilcrowIcon,
    PlusIcon,
    QuoteIcon,
    RadicalIcon,
    SquareIcon,
    TableIcon,
    TableOfContentsIcon,
    ImageIcon,
    FilmIcon,
    Cable,
    ExternalLink,
    Code2,
} from "lucide-react";
import { KEYS } from "platejs";
import { type PlateEditor, useEditorRef } from "platejs/react";
import DropdownMenu from "@/components/base/DropdownMenu";
import { insertBlock, insertInlineElement } from "@/components/Editor/transforms";
import { ToolbarButton, ToolbarMenuGroup } from "@/components/plate-ui/toolbar";
import { useTranslation } from "react-i18next";
import { INTERNAL_LINK_KEY } from "@/components/Editor/plugins/customs/internal-link/InternalLinkPlugin";

type Group = {
    group: string;
    items: Item[];
};

interface Item {
    icon: React.ReactNode;
    value: string;
    onSelect: (editor: PlateEditor, value: string) => void;
    focusEditor?: bool;
    label?: string;
}

const groups: Group[] = [
    {
        group: "editor.Basic blocks",
        items: [
            {
                icon: <PilcrowIcon className="size-4" />,
                label: "editor.Paragraph",
                value: KEYS.p,
            },
            {
                icon: <Heading1Icon className="size-4" />,
                label: "editor.Heading 1",
                value: "h1",
            },
            {
                icon: <Heading2Icon className="size-4" />,
                label: "editor.Heading 2",
                value: "h2",
            },
            {
                icon: <Heading3Icon className="size-4" />,
                label: "editor.Heading 3",
                value: "h3",
            },
            {
                icon: <TableIcon className="size-4" />,
                label: "editor.Table",
                value: KEYS.table,
            },
            {
                icon: <FileCodeIcon className="size-4" />,
                label: "editor.Code",
                value: KEYS.codeBlock,
            },
            {
                icon: <QuoteIcon className="size-4" />,
                label: "editor.Quote",
                value: KEYS.blockquote,
            },
            {
                icon: <MinusIcon className="size-4" />,
                label: "editor.Divider",
                value: KEYS.hr,
            },
        ].map((item) => ({
            ...item,
            onSelect: (editor, value) => {
                insertBlock(editor, value);
            },
        })),
    },
    {
        group: "editor.Lists",
        items: [
            {
                icon: <ListIcon className="size-4" />,
                label: "editor.Bulleted list",
                value: KEYS.ul,
            },
            {
                icon: <ListOrderedIcon className="size-4" />,
                label: "editor.Numbered list",
                value: KEYS.ol,
            },
            {
                icon: <SquareIcon className="size-4" />,
                label: "editor.To-do list",
                value: KEYS.listTodo,
            },
        ].map((item) => ({
            ...item,
            onSelect: (editor, value) => {
                insertBlock(editor, value);
            },
        })),
    },
    {
        group: "cloud.Media",
        items: [
            {
                icon: <ImageIcon className="size-4" />,
                label: "editor.Image",
                value: KEYS.img,
            },
            {
                icon: <FilmIcon className="size-4" />,
                label: "editor.Embed",
                value: KEYS.mediaEmbed,
            },
        ].map((item) => ({
            ...item,
            onSelect: (editor, value) => {
                insertBlock(editor, value);
            },
        })),
    },
    {
        group: "editor.Advanced blocks",
        items: [
            {
                icon: <TableOfContentsIcon className="size-4" />,
                label: "editor.Table of contents",
                value: KEYS.toc,
            },
            {
                icon: <LightbulbIcon className="size-4" />,
                label: "editor.Callout",
                value: KEYS.callout,
            },
            {
                icon: <RadicalIcon className="size-4" />,
                label: "editor.Equation",
                value: KEYS.equation,
            },
            {
                icon: <Code2 className="size-4" />,
                label: "editor.Code Drawing",
                value: KEYS.codeDrawing,
            },
        ].map((item) => ({
            ...item,
            onSelect: (editor, value) => {
                insertBlock(editor, value);
            },
        })),
    },
    {
        group: "editor.Inline",
        items: [
            {
                icon: <ExternalLink className="size-4" />,
                label: "editor.External link",
                value: KEYS.link,
            },
            {
                icon: <Cable className="size-4" />,
                label: "editor.Internal link",
                value: INTERNAL_LINK_KEY,
            },
            {
                focusEditor: true,
                icon: <CalendarIcon className="size-4" />,
                label: "editor.Date",
                value: KEYS.date,
            },
            {
                icon: <RadicalIcon className="size-4" />,
                label: "editor.Inline equation",
                value: KEYS.inlineEquation,
            },
        ].map((item) => ({
            ...item,
            onSelect: (editor, value) => {
                if (value === INTERNAL_LINK_KEY) {
                    editor.tf.insertText("{{");
                    return;
                }
                insertInlineElement(editor, value);
            },
        })),
    },
];

export function InsertToolbarButton(props: DropdownMenuProps) {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const [open, setOpen] = React.useState(false);

    return (
        <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false} {...props}>
            <DropdownMenu.Trigger asChild>
                <ToolbarButton pressed={open} tooltip={t("editor.Insert")} isDropdown>
                    <PlusIcon />
                </ToolbarButton>
            </DropdownMenu.Trigger>

            <DropdownMenu.Content className="flex max-h-[min(70vh,300px)] min-w-0 flex-col overflow-y-auto" align="start">
                {groups.map(({ group, items: nestedItems }) => (
                    <ToolbarMenuGroup key={group} label={t(group)}>
                        {nestedItems.map(({ icon, label, value, onSelect, focusEditor }) => (
                            <DropdownMenu.Item
                                key={value}
                                className="min-w-[180px]"
                                onSelect={() => {
                                    onSelect(editor, value);
                                    if (focusEditor) {
                                        editor.tf.focus();
                                    }
                                }}
                            >
                                {icon}
                                {label && t(label)}
                            </DropdownMenu.Item>
                        ))}
                    </ToolbarMenuGroup>
                ))}
            </DropdownMenu.Content>
        </DropdownMenu.Root>
    );
}
