import Collaborative from "@/components/Collaborative";
import Toast from "@/components/base/Toast";
import MoreMenu from "@/components/MoreMenu";
import useChangeCardChecklistTitle from "@/controllers/api/card/checklist/useChangeCardChecklistTitle";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

function BoardCardChecklistMoreMenuEdit(): React.JSX.Element {
    const { projectUID, card, sharedClassNames } = useBoardCard();
    const { model: checklist } = ModelRegistry.ProjectChecklist.useContext();
    const [t] = useTranslation();
    const { mutateAsync: changeChecklistTitleMutateAsync } = useChangeCardChecklistTitle({ interceptToast: true });
    const title = checklist.useField("title");
    const titleInputRef = useRef<HTMLInputElement>(null);

    const changeChecklistTitle = (endCallback: (shouldClose: bool) => void) => {
        if (!titleInputRef.current) {
            endCallback(false);
            return;
        }

        const newValue = titleInputRef.current.value.trim();

        if (!newValue) {
            Toast.Add.error(t("card.errors.Checklist title cannot be empty."));
            endCallback(false);
            titleInputRef.current.focus();
            return;
        }

        if (newValue === title) {
            endCallback(true);
            return;
        }

        const promise = changeChecklistTitleMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            checklist_uid: checklist.uid,
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
            onSave={changeChecklistTitle}
        >
            <BoardCardChecklistMoreMenuEditForm titleInputRef={titleInputRef} />
        </MoreMenu.PopoverItem>
    );
}

interface IBoardCardChecklistMoreMenuEditFormProps {
    titleInputRef: React.Ref<HTMLInputElement>;
}

function BoardCardChecklistMoreMenuEditForm({ titleInputRef }: IBoardCardChecklistMoreMenuEditFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const { card } = useBoardCard();
    const { model: checklist } = ModelRegistry.ProjectChecklist.useContext();
    const { save } = MoreMenu.useMoreMenuItem();
    const title = checklist.useField("title");

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            save();
        }
    };

    return (
        <Collaborative.Input
            collaborationType={EEditorCollaborationType.Card}
            uid={card.uid}
            section={`checklist-${checklist.uid}`}
            field="title"
            placeholder={t("card.Checklist title")}
            defaultValue={title}
            onKeyDown={handleKeyDown}
            ref={titleInputRef}
        />
    );
}

export default BoardCardChecklistMoreMenuEdit;
