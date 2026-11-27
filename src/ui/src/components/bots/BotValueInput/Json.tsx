import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import Popover from "@/components/base/Popover";
import Textarea from "@/components/base/Textarea";
import Toast from "@/components/base/Toast";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import JsonView from "@uiw/react-json-view";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { vscodeTheme } from "@uiw/react-json-view/vscode";
import { composeRefs } from "@/core/utils/ComponentUtils";
import { TSharedBotValueInputProps } from "@/components/bots/BotValueInput/types";

function BotValueJsonInput({ value, newValueRef, previewByDialog, isValidating, disabled, change, required, ref }: TSharedBotValueInputProps) {
    const [t] = useTranslation();
    const [currentObject, setCurrentObject] = useState(Utils.String.isJsonString(value) ? JSON.parse(value) : {});
    const [currentJSON, setCurrentJSON] = useState(Utils.String.isJsonString(value) ? JSON.stringify(currentObject, undefined, "    ") : value);
    const [error, setError] = useState<string>("");
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const viewerScrollableRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [isDialogOpened, setIsDialogOpened] = useState(false);

    const onValueChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        if (!textareaRef.current?.value || isValidating || disabled) {
            return;
        }

        const newValue = e.target.value;
        setCurrentJSON(newValue);

        if (
            textareaRef.current.value[0] !== "{" &&
            textareaRef.current.value[0] !== "}" &&
            textareaRef.current.value[0] !== "[" &&
            textareaRef.current.value[0] !== "]"
        ) {
            setError(t("metadata.errors.Invalid JSON format."));
            return;
        }

        try {
            const newObject = JSON.parse(textareaRef.current.value);
            setCurrentObject(newObject);
            setCurrentJSON(JSON.stringify(newObject, undefined, "    "));
            setError("");
        } catch (e) {
            if (e instanceof Error) {
                setError(e.message);
            }
            return;
        }

        setError("");
    };

    const save = () => {
        if (!textareaRef.current || isValidating || disabled) {
            return;
        }

        const isJson = Utils.String.isJsonString(textareaRef.current.value);
        newValueRef.current = isJson ? JSON.stringify(currentObject) : value;

        change?.();
    };

    useEffect(() => {
        if (!viewerScrollableRef.current || !textareaRef.current || previewByDialog) {
            return;
        }

        const onTextareaScroll = () => {
            viewerScrollableRef.current!.scrollTop = textareaRef.current!.scrollTop;
        };

        const onViewerScroll = () => {
            textareaRef.current!.scrollTop = viewerScrollableRef.current!.scrollTop;
        };
        textareaRef.current.addEventListener("scroll", onTextareaScroll);
        viewerScrollableRef.current.addEventListener("scroll", onViewerScroll);

        return () => {
            textareaRef.current?.removeEventListener("scroll", onTextareaScroll);
            viewerScrollableRef.current?.removeEventListener("scroll", onViewerScroll);
        };
    }, [value]);

    const onFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || !e.target.files.length || isValidating || disabled) {
            return;
        }

        const file = e.target.files[0];
        if (file.type !== "application/json") {
            return;
        }

        const reader = new FileReader();
        reader.onload = (event) => {
            if (!Utils.Type.isString(event.target?.result)) {
                return;
            }

            const newValue = event.target.result;
            if (Utils.String.isJsonString(newValue)) {
                const newObject = JSON.parse(newValue);
                setCurrentJSON(JSON.stringify(newObject, undefined, "    "));
                setCurrentObject(newObject);
                setError("");
                newValueRef.current = newValue;
                textareaRef.current?.focus();
            } else {
                Toast.Add.error(t("metadata.errors.Invalid JSON format."));
            }
        };
        reader.readAsText(file);
    };

    const valueInput = (
        <Textarea
            value={currentJSON}
            className="min-h-[calc(65vh_-_theme(spacing.28))] w-full p-0.5 text-[13px] leading-[1.32]"
            resize="none"
            onChange={onValueChange}
            disabled={isValidating || disabled}
            required={required}
            ref={composeRefs(textareaRef, ref as React.RefObject<HTMLTextAreaElement>)}
        />
    );

    const fileInput = (
        <Input type="file" className="hidden" ref={fileInputRef} accept=".json" onChange={onFileInputChange} disabled={isValidating || disabled} />
    );

    let jsonViewer;
    if (error.length) {
        jsonViewer = <Textarea value={error} readOnly className="border-none p-0 font-bold text-destructive focus-visible:ring-0" resize="none" />;
    } else {
        jsonViewer = (
            <Box
                className={cn(
                    "max-h-[calc(65vh_-_theme(spacing.28))] min-h-[calc(65vh_-_theme(spacing.28))] w-full overflow-y-auto",
                    !previewByDialog ? "max-w-[calc(50%_-_theme(spacing.1))]" : "max-w-full"
                )}
                ref={viewerScrollableRef}
            >
                <JsonView value={currentObject} style={vscodeTheme} className="w-full break-words" />
            </Box>
        );
    }

    if (previewByDialog) {
        return (
            <Box position="relative" w="full">
                {valueInput}
                <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="absolute right-10 top-1 gap-1 opacity-70"
                    onClick={save}
                    disabled={isValidating || disabled}
                >
                    <IconComponent icon="check" size="4" />
                    {t("common.Save")}
                </Button>
                <Popover.Root>
                    <Popover.Trigger asChild>
                        <Button variant="secondary" size="sm" className="absolute right-1.5 top-1 gap-1 opacity-70" disabled={isValidating}>
                            <IconComponent icon="ellipsis-vertical" size="4" />
                        </Button>
                    </Popover.Trigger>
                    <Popover.Content side="bottom" align="start" className="w-auto p-0">
                        <Flex direction="col">
                            {fileInput}
                            <Button
                                variant="ghost"
                                size="sm"
                                className="gap-1"
                                onClick={() => {
                                    fileInputRef.current?.click();
                                }}
                                disabled={isValidating || disabled}
                            >
                                <IconComponent icon="braces" size="4" />
                                {t("common.Upload JSON")}
                            </Button>
                            <Dialog.Root open={isDialogOpened} onOpenChange={setIsDialogOpened}>
                                <Dialog.Trigger asChild>
                                    <Button variant="ghost" size="sm" className="gap-1" disabled={isValidating}>
                                        <IconComponent icon="braces" size="4" />
                                        {t("common.JSON Viewer")}
                                    </Button>
                                </Dialog.Trigger>
                                <Dialog.Content className="max-h-[70vh] max-w-screen-md">
                                    <Dialog.Title>{t("common.JSON Viewer")}</Dialog.Title>
                                    <Dialog.Description asChild className="my-3 text-base text-primary-foreground">
                                        <Box>{jsonViewer}</Box>
                                    </Dialog.Description>
                                    <Dialog.Footer>
                                        <Button type="button" variant="secondary" size="sm" onClick={() => setIsDialogOpened(false)}>
                                            {t("common.Cancel")}
                                        </Button>
                                    </Dialog.Footer>
                                </Dialog.Content>
                            </Dialog.Root>
                        </Flex>
                    </Popover.Content>
                </Popover.Root>
            </Box>
        );
    }

    return (
        <Flex
            direction={{
                initial: "col",
                sm: "row",
            }}
            gap="2"
            w="full"
        >
            <Box position="relative" w="full" className="max-w-[calc(50%_-_theme(spacing.1))]">
                {valueInput}
                <Flex position="absolute" top="1" right="1.5" gap="1" className="opacity-70">
                    {fileInput}
                    <Button type="button" variant="secondary" size="sm" className="gap-1" onClick={save} disabled={isValidating || disabled}>
                        <IconComponent icon="check" size="4" />
                        {t("common.Save")}
                    </Button>
                    <Button
                        variant="secondary"
                        size="sm"
                        className="gap-1"
                        onClick={() => {
                            fileInputRef.current?.click();
                        }}
                        disabled={isValidating || disabled}
                    >
                        <IconComponent icon="braces" size="4" />
                        {t("common.Upload JSON")}
                    </Button>
                </Flex>
            </Box>
            {jsonViewer}
        </Flex>
    );
}

export default BotValueJsonInput;
