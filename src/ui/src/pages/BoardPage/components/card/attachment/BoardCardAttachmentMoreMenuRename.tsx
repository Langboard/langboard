import Floating from "@/components/base/Floating";
import Collaborative from "@/components/Collaborative";
import Toast from "@/components/base/Toast";
import MoreMenu from "@/components/MoreMenu";
import useChangeCardAttachmentName from "@/controllers/api/card/attachment/useChangeCardAttachmentName";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { ModelRegistry } from "@/core/models/ModelRegistry";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { EEditorCollaborationType } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

function BoardCardAttachmentMoreMenuRename(): React.JSX.Element {
    const { projectUID, card, sharedClassNames } = useBoardCard();
    const { model: attachment } = ModelRegistry.ProjectCardAttachment.useContext();
    const [t] = useTranslation();
    const { mutateAsync: changeCardAttachmentNameMutateAsync } = useChangeCardAttachmentName({ interceptToast: true });
    const nameInputRef = useRef<HTMLInputElement | null>(null);

    const changeAttachmentName = (endCallback: (shouldClose: bool) => void) => {
        if (!nameInputRef.current) {
            endCallback(false);
            return;
        }

        const nameValue = nameInputRef.current.value.trim();

        if (!nameValue) {
            Toast.Add.error(t("card.errors.File name cannot be empty."));
            endCallback(false);
            nameInputRef.current.focus();
            return;
        }

        const promise = changeCardAttachmentNameMutateAsync({
            project_uid: projectUID,
            card_uid: card.uid,
            attachment_uid: attachment.uid,
            attachment_name: nameValue,
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
                return t("successes.File name changed successfully.");
            },
            finally: () => {
                endCallback(true);
            },
        });
    };

    return (
        <MoreMenu.PopoverItem
            modal
            menuName={t("card.Rename")}
            contentProps={{ className: sharedClassNames.popoverContent }}
            onSave={changeAttachmentName}
        >
            <BoardCardAtthcmentMoreMenuRenameForm nameInputRef={nameInputRef} />
        </MoreMenu.PopoverItem>
    );
}

interface IBoardCardAtthcmentMoreMenuRenameFormProps {
    nameInputRef: React.Ref<HTMLInputElement>;
}

function BoardCardAtthcmentMoreMenuRenameForm({ nameInputRef }: IBoardCardAtthcmentMoreMenuRenameFormProps): React.JSX.Element {
    const [t] = useTranslation();
    const { card } = useBoardCard();
    const { model: attachment } = ModelRegistry.ProjectCardAttachment.useContext();
    const { save } = MoreMenu.useMoreMenuItem();
    const name = attachment.useField("name");
    const id = `floating-input-${Utils.String.Token.shortUUID()}`;

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            save();
        }
    };

    return (
        <div className="relative">
            <Collaborative.Input
                id={id}
                ref={nameInputRef}
                collaborationType={EEditorCollaborationType.Card}
                uid={card.uid}
                section={`attachment-${attachment.uid}`}
                field="name"
                defaultValue={name}
                placeholder=" "
                className="peer"
                onKeyDown={handleKeyDown}
            />
            <Floating.Label className="select-none" htmlFor={id}>
                {t("card.File name")}
            </Floating.Label>
        </div>
    );
}

export default BoardCardAttachmentMoreMenuRename;
