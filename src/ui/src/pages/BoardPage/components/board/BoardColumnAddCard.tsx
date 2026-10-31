import Box from "@/components/base/Box";
import Textarea from "@/components/base/Textarea";
import { useBoardAddCard } from "@/core/providers/BoardAddCardProvider";
import { cn, measureTextAreaHeight } from "@/core/utils/ComponentUtils";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const BoardColumnAddCard = memo(() => {
    const {
        isEditing,
        isValidating,
        changeMode,
        scrollToBottom,
        canWrite,
        textareaRef,
        disableChangeModeAttr,
        saveTitleDraft,
        description,
        saveDescriptionDraft,
    } = useBoardAddCard();
    const [t] = useTranslation();
    const [height, setHeight] = useState(0);

    return (
        <>
            {!isEditing || !canWrite ? (
                <></>
            ) : (
                <Box
                    mt="1.5"
                    py="1"
                    pl="2"
                    pr="1"
                    rounded="md"
                    mb="-2.5"
                    className="bg-secondary/70"
                    {...{ [disableChangeModeAttr]: true }}
                    onClick={(e) => {
                        if ((e.target as HTMLElement) === e.currentTarget) {
                            textareaRef.current?.focus();
                        }
                    }}
                >
                    <Textarea
                        ref={textareaRef}
                        className={cn(
                            "max-h-28 min-h-12 resize-none break-all border-none bg-transparent p-0 scrollbar-hide",
                            "focus-visible:shadow-none focus-visible:ring-transparent focus-visible:ring-offset-transparent"
                        )}
                        style={{ height }}
                        aria-label={t("board.Enter a title")}
                        placeholder={t("board.Enter a title")}
                        disabled={isValidating}
                        onChange={(event) => {
                            saveTitleDraft(event.currentTarget.value);
                            setHeight(measureTextAreaHeight(textareaRef.current!));
                            setTimeout(() => {
                                scrollToBottom();
                            }, 0);
                        }}
                        onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                                e.preventDefault();
                                e.stopPropagation();
                                changeMode("view");
                                return;
                            }
                        }}
                    />
                    <details open={description.length > 0 || undefined} className="mt-1 text-sm">
                        <summary className="cursor-pointer py-1">{t("board.Description (optional)")}</summary>
                        <Textarea
                            aria-label={t("board.Description (optional)")}
                            placeholder={t("board.Enter a description (Markdown supported)")}
                            className="max-h-48 min-h-24 resize-none"
                            rows={4}
                            value={description}
                            disabled={isValidating}
                            onChange={(event) => saveDescriptionDraft(event.currentTarget.value)}
                        />
                    </details>
                </Box>
            )}
        </>
    );
});
BoardColumnAddCard.displayName = "Board.ColumnAddCard";

export default BoardColumnAddCard;
