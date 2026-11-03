/* eslint-disable @/max-len */
/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import * as React from "react";
import type { DropdownMenuProps } from "@radix-ui/react-dropdown-menu";
import { PlaceholderPlugin } from "@platejs/media/react";
import { AudioLinesIcon, FileUpIcon, FilmIcon, ImageIcon, LinkIcon } from "lucide-react";
import { isUrl, KEYS } from "platejs";
import { useEditorRef } from "platejs/react";
import { useFilePicker } from "use-file-picker";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/plate-ui/alert-dialog";
import { ToolbarSplitButton, ToolbarSplitButtonPrimary, ToolbarSplitButtonSecondary } from "@/components/plate-ui/toolbar";
import DropdownMenu from "@/components/base/DropdownMenu";
import Floating from "@/components/base/Floating";
import Toast from "@/components/base/Toast";
import { useTranslation } from "react-i18next";

const MEDIA_CONFIG: Record<
    string,
    {
        accept: string[];
        icon: React.ReactNode;
        title: string;
        tooltip: string;
    }
> = {
    [KEYS.audio]: {
        accept: ["audio/*"],
        icon: <AudioLinesIcon className="size-4" />,
        title: "editor.Insert Audio",
        tooltip: "editor.Audio",
    },
    [KEYS.file]: {
        accept: ["*"],
        icon: <FileUpIcon className="size-4" />,
        title: "editor.Insert File",
        tooltip: "editor.File",
    },
    [KEYS.img]: {
        accept: ["image/*"],
        icon: <ImageIcon className="size-4" />,
        title: "editor.Insert Image",
        tooltip: "editor.Image",
    },
    [KEYS.video]: {
        accept: ["video/*"],
        icon: <FilmIcon className="size-4" />,
        title: "editor.Insert Video",
        tooltip: "editor.Video",
    },
};

export function MediaToolbarButton({ nodeType, ...props }: DropdownMenuProps & { nodeType: string }) {
    const [t] = useTranslation();
    const currentConfig = MEDIA_CONFIG[nodeType];

    const editor = useEditorRef();
    const [open, setOpen] = React.useState(false);
    const [dialogOpen, setDialogOpen] = React.useState(false);

    const { openFilePicker } = useFilePicker({
        accept: currentConfig.accept,
        multiple: true,
        onFilesSelected: (({ plainFiles: updatedFiles }: { plainFiles: FileList }) => {
            editor.getTransforms(PlaceholderPlugin).insert.media(updatedFiles);
        }) as any,
    });

    return (
        <>
            <ToolbarSplitButton
                onClick={() => {
                    openFilePicker();
                }}
                onKeyDown={(e) => {
                    if (e.key === "ArrowDown") {
                        e.preventDefault();
                        setOpen(true);
                    }
                }}
                pressed={open}
            >
                <ToolbarSplitButtonPrimary aria-label={t(currentConfig.title)}>{currentConfig.icon}</ToolbarSplitButtonPrimary>

                <DropdownMenu.Root open={open} onOpenChange={setOpen} modal={false} {...props}>
                    <DropdownMenu.Trigger asChild>
                        <ToolbarSplitButtonSecondary aria-label={t(currentConfig.tooltip)} />
                    </DropdownMenu.Trigger>

                    <DropdownMenu.Content onClick={(e) => e.stopPropagation()} align="start" alignOffset={-32}>
                        <DropdownMenu.Group>
                            <DropdownMenu.Item onSelect={() => openFilePicker()}>
                                {currentConfig.icon}
                                {t("editor.Upload from computer")}
                            </DropdownMenu.Item>
                            <DropdownMenu.Item onSelect={() => setDialogOpen(true)}>
                                <LinkIcon className="size-4" />
                                {t("editor.Insert via URL")}
                            </DropdownMenu.Item>
                        </DropdownMenu.Group>
                    </DropdownMenu.Content>
                </DropdownMenu.Root>
            </ToolbarSplitButton>

            <AlertDialog
                open={dialogOpen}
                onOpenChange={(value) => {
                    setDialogOpen(value);
                }}
            >
                <AlertDialogContent className="gap-6">
                    <MediaUrlDialogContent currentConfig={currentConfig} nodeType={nodeType} isOpened={dialogOpen} setOpen={setDialogOpen} />
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}

function MediaUrlDialogContent({
    currentConfig,
    nodeType,
    isOpened,
    setOpen,
}: {
    currentConfig: (typeof MEDIA_CONFIG)[string];
    nodeType: string;
    isOpened: bool;
    setOpen: (value: bool) => void;
}) {
    const [t] = useTranslation();
    const editor = useEditorRef();
    const [url, setUrl] = React.useState("");
    const inputRef = React.useRef<HTMLInputElement>(null);

    const embedMedia = React.useCallback(() => {
        if (!isUrl(url)) return Toast.Add.error(t("editor.errors.invalid.url"));

        setOpen(false);
        editor.tf.insertNodes({
            children: [{ text: "" }],
            name: nodeType === KEYS.file ? url.split("/").pop() : undefined,
            type: nodeType,
            url,
        });
    }, [url, editor, nodeType, setOpen]);

    React.useEffect(() => {
        if (isOpened && inputRef.current) {
            setTimeout(() => {
                inputRef.current!.focus();
            }, 0);
        }
    }, [isOpened]);

    return (
        <>
            <AlertDialogHeader>
                <AlertDialogTitle>{t(currentConfig.title)}</AlertDialogTitle>
            </AlertDialogHeader>

            <AlertDialogDescription className="group relative w-full">
                <label
                    className="absolute top-1/2 block -translate-y-1/2 cursor-text px-1 text-sm text-muted-foreground/70 transition-all group-focus-within:pointer-events-none group-focus-within:top-0 group-focus-within:cursor-default group-focus-within:text-xs group-focus-within:font-medium group-focus-within:text-foreground has-[+input:not(:placeholder-shown)]:pointer-events-none has-[+input:not(:placeholder-shown)]:top-0 has-[+input:not(:placeholder-shown)]:cursor-default has-[+input:not(:placeholder-shown)]:text-xs has-[+input:not(:placeholder-shown)]:font-medium has-[+input:not(:placeholder-shown)]:text-foreground"
                    htmlFor="url"
                >
                    <span className="inline-flex bg-background px-2">URL</span>
                </label>
                <Floating.LabelInput
                    id="url"
                    className="w-full"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter") embedMedia();
                    }}
                    label={t("editor.URL")}
                    placeholder={t("editor.Paste link")}
                    type="url"
                    ref={inputRef}
                />
            </AlertDialogDescription>

            <AlertDialogFooter>
                <AlertDialogCancel>{t("editor.Cancel")}</AlertDialogCancel>
                <AlertDialogAction
                    onClick={(e) => {
                        e.preventDefault();
                        embedMedia();
                    }}
                >
                    {t("editor.Accept")}
                </AlertDialogAction>
            </AlertDialogFooter>
        </>
    );
}
