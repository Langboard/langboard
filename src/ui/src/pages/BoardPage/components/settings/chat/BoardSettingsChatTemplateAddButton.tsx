import SubmitButton from "@/components/base/SubmitButton";
import Toast from "@/components/base/Toast";
import useCreateProjectChatTemplate from "@/controllers/api/board/chat/useCreateProjectChatTemplate";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { useBoardSettings } from "@/core/providers/BoardSettingsProvider";
import { useState } from "react";
import { useTranslation } from "react-i18next";

function BoardSettingsChatTemplateAddButton() {
    const { project } = useBoardSettings();
    const [t] = useTranslation();
    const [isValidating, setIsValidating] = useState(false);
    const { mutateAsync: createProjectChatTemplateMutateAsync } = useCreateProjectChatTemplate({ interceptToast: true });

    const createChatTemplate = () => {
        if (isValidating) {
            return;
        }

        setIsValidating(true);

        const promise = createProjectChatTemplateMutateAsync({
            project_uid: project.uid,
            name: t("project.settings.New chat template name"),
            template: t("project.settings.Sample chat template text"),
        });

        Toast.Add.promise(promise, {
            loading: t("common.Adding..."),
            error: (error) => {
                const messageRef = { message: "" };
                const { handle } = setupApiErrorHandler({}, messageRef);

                handle(error);
                return messageRef.message;
            },
            success: () => {
                return t("successes.Chat template added successfully.");
            },
            finally: () => {
                setIsValidating(false);
            },
        });
    };

    return (
        <SubmitButton
            type="button"
            isValidating={isValidating}
            variant="outline"
            className="mb-4 w-full border-2 border-dashed"
            onClick={createChatTemplate}
        >
            {t("project.settings.Add a template")}
        </SubmitButton>
    );
}

export default BoardSettingsChatTemplateAddButton;
