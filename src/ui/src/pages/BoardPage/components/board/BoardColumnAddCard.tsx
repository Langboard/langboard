import Box from "@/components/base/Box";
import Textarea from "@/components/base/Textarea";
import { useBoardAddCard } from "@/core/providers/BoardAddCardProvider";
import { cn, measureTextAreaHeight } from "@/core/utils/ComponentUtils";
import { memo, useState } from "react";
import { useTranslation } from "react-i18next";

const BoardColumnAddCard = memo(() => {
    const { isEditing, isValidating, changeMode, scrollToBottom, canWrite, textareaRef, disableChangeModeAttr, saveTitleDraft } = useBoardAddCard();
    const [t] = useTranslation();
    const [height, setHeight] = useState(0);

    return (
        <>
            {!isEditing || !canWrite ? (
                <></>
            ) : (
                <Box
                    mt="1.5"
                    maxH="28"
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
                            "min-h-12 resize-none break-all border-none bg-transparent p-0 scrollbar-hide",
                            "focus-visible:shadow-none focus-visible:ring-transparent focus-visible:ring-offset-transparent"
                        )}
                        style={{ height }}
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
                            if (e.key === "Enter") {
                                e.preventDefault();
                                e.stopPropagation();
                                changeMode("view");
                                return;
                            }
                        }}
                    />
                </Box>
            )}
        </>
    );
});
BoardColumnAddCard.displayName = "Board.ColumnAddCard";

export default BoardColumnAddCard;
