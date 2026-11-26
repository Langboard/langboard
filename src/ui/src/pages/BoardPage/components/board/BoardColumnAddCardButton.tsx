import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import SubmitButton from "@/components/base/SubmitButton";
import { useBoardAddCard } from "@/core/providers/BoardAddCardProvider";
import { memo } from "react";
import { useTranslation } from "react-i18next";

const BoardColumnAddCardButton = memo(() => {
    const { isEditing, setIsEditing, isValidating, changeMode, canWrite, privateDefault, textareaRef, disableChangeModeAttr } = useBoardAddCard();
    const [t] = useTranslation();

    if (!canWrite) {
        return null;
    }

    const save = () => {
        const newValue = textareaRef.current?.value?.replace(/\n/g, " ").trim() ?? "";
        if (!newValue.length) {
            textareaRef.current?.focus();
            return;
        }

        changeMode("view");
    };

    return (
        <>
            {!isEditing ? (
                <Button variant="ghost" className="w-full justify-start gap-2 p-2" onClick={() => changeMode("edit")}>
                    <IconComponent icon="plus" size="5" />
                    {t("board.Add a card")}
                </Button>
            ) : (
                <Flex items="center" gap="2" className="flex-wrap" {...{ [disableChangeModeAttr]: true }}>
                    <SubmitButton type="button" className="h-8 px-3 py-2" isValidating={isValidating} onClick={save}>
                        {t("board.Add card")}
                    </SubmitButton>
                    <Button variant="ghost" size="icon-sm" disabled={isValidating} onClick={() => setIsEditing(false)}>
                        <IconComponent icon="x" size="5" />
                    </Button>
                    {privateDefault && (
                        <span className="badge badge-outline badge-sm gap-1" role="status">
                            <IconComponent icon="lock" size="3" aria-hidden />
                            {t("card.Private")}
                        </span>
                    )}
                </Flex>
            )}
        </>
    );
});
BoardColumnAddCardButton.displayName = "Board.ColumnAddCardButton";

export default BoardColumnAddCardButton;
