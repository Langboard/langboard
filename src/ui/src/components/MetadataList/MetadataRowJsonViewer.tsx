import Collaborative from "@/components/Collaborative";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import ScrollArea from "@/components/base/ScrollArea";
import Textarea from "@/components/base/Textarea";
import { useTranslation } from "react-i18next";
import JsonView from "@uiw/react-json-view";
import { vscodeTheme } from "@uiw/react-json-view/vscode";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/core/utils/ComponentUtils";
import { TEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";

export interface IMetadataRowJsonViewerProps {
    collaborationType: TEditorCollaborationType;
    uid: string;
    section: string;
    valueInputRef: React.RefObject<HTMLInputElement | null>;
    handleValueInput: () => void;
    currentValue: string;
    canEdit: () => bool;
}

function MetadataRowJsonViewer({
    collaborationType,
    uid,
    section,
    valueInputRef,
    handleValueInput,
    currentValue,
    canEdit,
}: IMetadataRowJsonViewerProps): React.JSX.Element {
    const [t] = useTranslation();
    const [currentObject, setCurrentObject] = useState(Utils.String.isJsonString(currentValue) ? JSON.parse(currentValue) : {});
    const [currentJSON, setCurrentJSON] = useState(
        Utils.String.isJsonString(currentValue) ? JSON.stringify(currentObject, undefined, "    ") : currentValue
    );
    const [isOpened, setIsOpened] = useState(false);
    const [error, setError] = useState<string>("");
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const onChange = useCallback(() => {
        if (!textareaRef.current?.value) {
            return;
        }

        setCurrentJSON(textareaRef.current.value);

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
    }, [setCurrentJSON, setCurrentObject]);
    const update = useCallback(() => {
        if (!canEdit() || !valueInputRef.current || !textareaRef.current) {
            return;
        }

        const isJson = Utils.String.isJsonString(textareaRef.current.value);
        const newValue = isJson ? JSON.stringify(JSON.parse(textareaRef.current.value)) : textareaRef.current.value;
        valueInputRef.current.value = newValue;
        handleValueInput();
        setIsOpened(false);
    }, [canEdit, handleValueInput, setIsOpened]);

    useEffect(() => {
        if (!Utils.String.isJsonString(currentValue)) {
            onChange();
            return;
        }

        const newObject = JSON.parse(currentValue);
        setCurrentObject(newObject);
        setCurrentJSON(JSON.stringify(newObject, undefined, "    "));
    }, [currentValue]);

    return (
        <Dialog.Root open={isOpened} onOpenChange={setIsOpened}>
            <Dialog.Trigger asChild>
                <Button
                    variant="ghost"
                    size="icon-sm"
                    title={t(`common.JSON ${canEdit() ? "Editor" : "Viewer"}`)}
                    titleAlign="end"
                    className="absolute right-0 top-1/2 h-8 w-7 -translate-y-1/2 rounded-l-none"
                >
                    <IconComponent icon="braces" size="4" />
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="max-h-[70vh] max-w-screen-md">
                <Dialog.Title>{t(`common.JSON ${canEdit() ? "Editor" : "Viewer"}`)}</Dialog.Title>
                <Dialog.Description asChild className="my-3 text-base text-primary-foreground">
                    <Flex
                        direction={{
                            initial: "col",
                            sm: "row",
                        }}
                        gap="2"
                    >
                        {canEdit() && (
                            <div className="w-full max-w-[calc(50%_-_theme(spacing.1))]">
                                <Collaborative.Textarea
                                    collaborationType={collaborationType}
                                    uid={uid}
                                    section={section}
                                    field="value"
                                    defaultValue={currentJSON}
                                    className={cn("min-h-[calc(70vh_-_theme(spacing.28))] p-0.5 text-[13px] leading-[1.32]")}
                                    resize="none"
                                    onValueChange={setCurrentJSON}
                                    onChange={onChange}
                                    ref={textareaRef}
                                />
                            </div>
                        )}
                        {error.length ? (
                            <Textarea
                                value={error}
                                readOnly
                                className="border-none p-0 font-bold text-destructive focus-visible:ring-0"
                                resize="none"
                            />
                        ) : (
                            <ScrollArea.Root
                                className={cn(
                                    "max-h-[calc(70vh_-_theme(spacing.28))] min-h-[calc(70vh_-_theme(spacing.28))] w-full",
                                    canEdit() ? "max-w-[calc(50%_-_theme(spacing.1))]" : "max-w-full"
                                )}
                            >
                                <JsonView value={currentObject} style={vscodeTheme} className="w-full break-words" />
                            </ScrollArea.Root>
                        )}
                    </Flex>
                </Dialog.Description>
                <Dialog.Footer>
                    <Button type="button" variant="secondary" size="sm" onClick={() => setIsOpened(false)}>
                        {t("common.Cancel")}
                    </Button>
                    {canEdit() && (
                        <Button type="button" size="sm" onClick={update}>
                            {t("common.Save")}
                        </Button>
                    )}
                </Dialog.Footer>
            </Dialog.Content>
        </Dialog.Root>
    );
}

export default MetadataRowJsonViewer;
