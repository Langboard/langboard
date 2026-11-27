import Collaborative from "@/components/Collaborative";
import Toast from "@/components/base/Toast";
import MoreMenu from "@/components/MoreMenu";
import useChangeCardCheckitemTitle from "@/controllers/api/card/checkitem/useChangeCardCheckitemTitle";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

function BoardCardCheckitemMoreMenuEdit(): React.JSX.Element {
    const { projectUID, card, sharedClassNames } = useBoardCard();
    const { model: checkitem } = ModelRegistry.ProjectCheckitem.useContext();
    const [t] = useTranslation();
    const { mutateAsync: changeCheckitemTitleMutateAsync } = useChangeCardCheckitemTitle({ interceptToast: true });
    const title = checkitem.useField("title");
    const titleInputRef = useRef<HTMLInputElement>(null);

    const changeCheckitemTitle = (endCallback: (shouldClose: bool) => void) => {
        if (!titleInputRef.current) {
            endCallback(false);
            return;
        }

        const newValue = titleInputRef.current.value.trim();

        if (!newValue) {
            Toast.Add.error(t("card.errors.Checkitem title cannot be empty."));
            endCallback(false);
            titleInputRef.current.focus();
            return;
        }

        if (newValue === title) {
            endCallback(true);
            return;
        }

        const promise = changeCheckitemTitleMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            checkitem_uid: checkitem.uid,
            title: newValue,
        });

        Toast.Add.promise(promise, {
            loading: t("common.Changing..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Title changed successfully.");
            },
            finally: () => {
                endCallback(true);
            },
        });
    };

    return (
        <MoreMenu.PopoverItem
            modal
            menuName={t("card.Edit title")}
            contentProps={{ className: sharedClassNames.popoverContent }}
            onSave={changeCheckitemTitle}
        >
            <BoardCardCheckitemMoreMenuEditForm titleInputRef={titleInputRef} />
        </MoreMenu.PopoverItem>
    );
}

interface IBoardCardCheckitemMoreMenuEditFormProps {
    titleInputRef: React.Ref<HTMLInputElement>;
}

function BoardCardCheckitemMoreMenuEditForm({ titleInputRef }: IBoardCardCheckitemMoreMenuEditFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const { card } = useBoardCard();
    const { model: checkitem } = ModelRegistry.ProjectCheckitem.useContext();
    const { save } = MoreMenu.useMoreMenuItem();
    const title = checkitem.useField("title");

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            save();
        }
    };

    return (
        <Collaborative.Input
            collaborationType={EEditorCollaborationType.Card}
            uid={card.uid}
            section={`checkitem-${checkitem.uid}`}
            field="title"
            placeholder={t("card.Checkitem title")}
            defaultValue={title}
            onKeyDown={handleKeyDown}
            ref={titleInputRef}
        />
    );
}

export default BoardCardCheckitemMoreMenuEdit;
