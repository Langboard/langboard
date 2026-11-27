import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import Input from "@/components/base/Input";
import { useBoardChat } from "@/core/providers/BoardChatProvider";
import { useTranslation } from "react-i18next";
import { useChatInput } from "@/pages/BoardPage/components/chat/ChatInputProvider";
import { cn } from "@/core/utils/ComponentUtils";

export interface IChatInputFileUploadProps {
    className?: string;
    showLabel?: bool;
}

function ChatInputFileUpload({ className, showLabel = false }: IChatInputFileUploadProps) {
    const { isSending } = useBoardChat();
    const { chatAttachmentRef, setFile } = useChatInput();
    const [t] = useTranslation();

    const onAttachmentChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (!e.target.files || e.target.files.length === 0) {
            setFile(null);
            return;
        }

        const file = e.target.files[0];
        setFile(() => file);
    };

    return (
        <>
            <Input
                type="file"
                hidden
                accept="image/*, .txt, .xls, .xlsx"
                disabled={isSending}
                onChange={onAttachmentChange}
                ref={chatAttachmentRef}
                wrapperProps={{ className: "hidden" }}
            />
            <Button
                type="button"
                variant="ghost"
                size={showLabel ? "sm" : "icon-sm"}
                className={cn(showLabel && "justify-start gap-2", className)}
                title={showLabel ? undefined : t("common.Upload")}
                disabled={isSending}
                titleSide="top"
                titleAlign="start"
                onClick={() => chatAttachmentRef.current?.click()}
            >
                <IconComponent icon="paperclip" size="4" />
                {showLabel && <span>{t("common.Upload")}</span>}
            </Button>
        </>
    );
}

export default ChatInputFileUpload;
