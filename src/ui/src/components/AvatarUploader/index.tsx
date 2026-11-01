import { useCallback, useEffect, useRef, useState } from "react";
import { useDropzone } from "react-dropzone";
import { useTranslation } from "react-i18next";
import { VariantProps } from "tailwind-variants";
import FormErrorMessage from "@/components/FormErrorMessage";
import Avatar from "@/components/base/Avatar";
import Box from "@/components/base/Box";
import Dock from "@/components/base/Dock";
import Flex from "@/components/base/Flex";
import Form from "@/components/base/Form";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import Tooltip from "@/components/base/Tooltip";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";

interface IBaseAvatarUploaderProps {
    name?: string;
    isBot?: bool;
    userInitials?: string;
    errorMessage?: string;
    initialAvatarUrl?: string;
    dataTransferRef: React.RefObject<DataTransfer>;
    avatarUrlRef?: React.RefObject<string | null>;
    isDeletedRef?: React.RefObject<bool>;
    isValidating?: bool;
    disabled?: bool;
    canRevertUrl?: bool;
    avatarSize?: VariantProps<typeof Avatar.Variants>["size"];
    hideDock?: bool;
    notInForm?: bool;
    rootClassName?: string;
    onChange?: (files: File[] | FileList) => void;
    onDeleted?: () => void;
    selectedFile?: File;
}

interface IUserAvatarUploaderProps extends IBaseAvatarUploaderProps {
    isBot?: false;
    userInitials: string;
}

interface IBotAvatarUploaderProps extends IBaseAvatarUploaderProps {
    isBot: true;
    userInitials?: never;
}

export type TAvatarUploaderProps = IUserAvatarUploaderProps | IBotAvatarUploaderProps;

function AvatarUploader({
    name = "avatar",
    isBot,
    userInitials,
    initialAvatarUrl,
    dataTransferRef,
    avatarUrlRef,
    isDeletedRef,
    isValidating,
    disabled,
    errorMessage,
    canRevertUrl = false,
    avatarSize = "2xl",
    hideDock = false,
    notInForm,
    rootClassName,
    onChange,
    onDeleted,
    selectedFile,
}: TAvatarUploaderProps): React.JSX.Element {
    const [t] = useTranslation();
    const [avatarUrl, setAvatarUrl] = useState<string | undefined>(initialAvatarUrl);
    const readVersionRef = useRef(0);
    const initialUrlRef = useRef(initialAvatarUrl);
    const handleUpload = useCallback(
        (files: File[] | FileList) => {
            if (isValidating || disabled || !files.length) {
                return;
            }

            const file = files[0];
            const readVersion = ++readVersionRef.current;
            dataTransferRef.current.items.clear();
            dataTransferRef.current.items.add(file);
            if (isDeletedRef) {
                isDeletedRef.current = false;
            }

            const reader = new FileReader();
            reader.onload = () => {
                if (readVersion !== readVersionRef.current) {
                    return;
                }
                if (avatarUrlRef) {
                    avatarUrlRef.current = reader.result as string;
                }
                setAvatarUrl(reader.result as string);
            };
            reader.readAsDataURL(file);
            onChange?.([file]);
        },
        [isValidating, disabled, dataTransferRef, isDeletedRef, avatarUrlRef, onChange]
    );
    const acceptedFile = useRef<File | undefined>(undefined);
    useEffect(() => {
        if (!selectedFile || selectedFile === acceptedFile.current || isValidating || disabled) return;
        acceptedFile.current = selectedFile;
        handleUpload([selectedFile]);
    }, [selectedFile, isValidating, disabled, handleUpload]);
    const { getRootProps, getInputProps, inputRef, isDragActive } = useDropzone({
        accept: { "image/*": [] },
        onDrop: handleUpload,
        multiple: false,
        disabled: isValidating || disabled,
    });
    const [bgColor, textColor] = isBot ? ["", ""] : new Utils.Color.Generator(userInitials).generateAvatarColor();

    const styles: Record<string, string> = {
        "--avatar-bg": bgColor,
        "--avatar-text-color": textColor,
    };

    const removeAvatar = () => {
        if (isValidating || disabled) {
            return;
        }

        ++readVersionRef.current;
        if (isDeletedRef) {
            isDeletedRef.current = true;
        }
        dataTransferRef.current.items.clear();
        if (avatarUrlRef) {
            avatarUrlRef.current = null;
        }
        setAvatarUrl(undefined);
        onDeleted?.();
    };

    const revertUrl = () => {
        if (isValidating || disabled) {
            return;
        }

        ++readVersionRef.current;
        dataTransferRef.current.items.clear();
        if (avatarUrlRef) {
            avatarUrlRef.current = initialAvatarUrl ?? null;
        }
        if (isDeletedRef) {
            isDeletedRef.current = false;
        }
        setAvatarUrl(initialAvatarUrl);
    };

    useEffect(() => {
        ++readVersionRef.current;
        if (initialUrlRef.current !== initialAvatarUrl) {
            dataTransferRef.current.items.clear();
            if (isDeletedRef) {
                isDeletedRef.current = false;
            }
            if (avatarUrlRef) {
                avatarUrlRef.current = initialAvatarUrl ?? null;
            }
            initialUrlRef.current = initialAvatarUrl;
        }
        setAvatarUrl(initialAvatarUrl);
        return () => {
            ++readVersionRef.current;
        };
    }, [initialAvatarUrl, dataTransferRef, isDeletedRef, avatarUrlRef]);

    const avatar = (
        <>
            <Flex justify="center" position="relative" cursor="pointer" className="transition-all duration-200 hover:opacity-80">
                <Avatar.Root size={avatarSize}>
                    <Avatar.Image src={avatarUrl} alt="" />
                    <Avatar.Fallback style={styles} className={cn("text-4xl", !isBot && "bg-[--avatar-bg] text-[--avatar-text-color]")}>
                        {isBot ? <IconComponent icon="bot" className="size-2/3" /> : userInitials}
                    </Avatar.Fallback>
                </Avatar.Root>
                <Input
                    wrapperProps={{ className: "hidden" }}
                    {...getInputProps()}
                    disabled={isValidating || disabled}
                    isFormControl={!notInForm}
                    ref={inputRef}
                />
            </Flex>
            {errorMessage && <FormErrorMessage error={errorMessage} icon="circle-alert" wrapperClassName="justify-center" notInForm={notInForm} />}
        </>
    );

    return (
        <Box position="relative" w="full" className={rootClassName} {...getRootProps()}>
            {isDragActive && (
                <Flex
                    items="center"
                    justify="center"
                    size="full"
                    position="absolute"
                    left="0"
                    top="0"
                    z="50"
                    className="border-2 border-dashed border-primary bg-background/70"
                >
                    {t("user.Drop avatar here")}
                </Flex>
            )}
            {!notInForm ? <Form.Field name={name}>{avatar}</Form.Field> : <Box>{avatar}</Box>}
            {!hideDock && !disabled && (
                <Dock.Root direction="middle" magnification={50} distance={100} size="sm">
                    <Dock.Button
                        buttonProps={{ type: "button", className: "p-3", onClick: () => !isValidating && inputRef.current?.click() }}
                        dockIconProps={{ className: "bg-accent/70 transition-colors duration-300 hover:text-primary" }}
                        title={t("user.Upload avatar")}
                        titleSide="bottom"
                        icon="upload"
                    />
                    {canRevertUrl && avatarUrl !== initialAvatarUrl && (
                        <Dock.Button
                            buttonProps={{ type: "button", className: "p-3", onClick: revertUrl }}
                            dockIconProps={{ className: "bg-accent/70 transition-colors duration-300 hover:text-primary" }}
                            title={t("user.Revert avatar")}
                            titleSide="bottom"
                            icon="undo-2"
                        />
                    )}
                    {avatarUrl && (
                        <Dock.Icon className="bg-accent/70 text-red-500 transition-colors duration-300 hover:text-red-700">
                            <Tooltip.Root disableHoverableContent>
                                <Tooltip.Trigger className="p-3" type="button">
                                    <IconComponent icon="trash-2" className="size-full" onClick={removeAvatar} />
                                </Tooltip.Trigger>
                                <Tooltip.Content side="bottom">{t("user.Delete avatar")}</Tooltip.Content>
                            </Tooltip.Root>
                        </Dock.Icon>
                    )}
                </Dock.Root>
            )}
        </Box>
    );
}

export default AvatarUploader;
