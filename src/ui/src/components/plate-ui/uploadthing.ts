import { useNativeAttachmentUpload } from "@/components/AttachmentDuplicateDecision";
import { EEditorType } from "@langboard/core/constants";
import Toast from "@/components/base/Toast";
import { api } from "@/core/helpers/Api";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { useEditorData } from "@/core/providers/EditorDataProvider";
import { Utils } from "@langboard/core/utils";
import { EHttpStatus } from "@langboard/core/enums";
import { AxiosProgressEvent } from "axios";
import * as React from "react";
import { useTranslation } from "react-i18next";

export interface UploadedFile {
    name: string;
    url: string;
}

export function useUploadFile() {
    const [t] = useTranslation();
    const { uploadPath, uploadedCallback, currentUser, editorType, form, documentID } = useEditorData();
    const isCardMedia =
        editorType === EEditorType.CardDescription || editorType === EEditorType.CardComment || editorType === EEditorType.CardNewComment;
    const source = JSON.stringify([currentUser?.uid, editorType, uploadPath, documentID]);
    const { upload, decisionDialog } = useNativeAttachmentUpload(source);
    const sourceRef = React.useRef(source);
    sourceRef.current = source;
    const [failedFile, setFailedFile] = React.useState<File>();
    const [uploadedFile, setUploadedFile] = React.useState<{ source: string; file: UploadedFile }>();
    const [uploadingFile, setUploadingFile] = React.useState<File>();
    const [progress, setProgress] = React.useState<number>(0);
    const [isUploading, setIsUploading] = React.useState(false);

    const previousSource = React.useRef(source);
    React.useEffect(() => {
        if (previousSource.current === source) return;
        previousSource.current = source;
        setFailedFile(undefined);
        setUploadedFile(undefined);
        setIsUploading(false);
        setUploadingFile(undefined);
        setProgress(0);
    }, [source]);

    async function uploadThing(file: File) {
        if (!uploadPath) {
            return;
        }

        const activeSource = source;
        setFailedFile(undefined);
        setUploadedFile(undefined);
        setIsUploading(true);
        setUploadingFile(file);

        const formData = new FormData();
        formData.append("attachment", file);

        try {
            const uploadURL = Utils.String.convertServerFileURL(uploadPath);
            const onUploadProgress = (event: AxiosProgressEvent) => {
                if (sourceRef.current !== activeSource) return;
                setProgress(event.total ? Math.min(100, (event.loaded / event.total) * 100) : 0);
            };
            const data = isCardMedia
                ? await upload(uploadURL, form.card_uid as string, file, onUploadProgress)
                : (await api.post(uploadURL, formData, { onUploadProgress })).data;
            if (!data || sourceRef.current !== activeSource) return;

            const { name, url } = data;
            const uploadedFile = {
                name,
                url: Utils.String.convertServerFileURL(url as string),
            };

            setUploadedFile({ source: activeSource, file: uploadedFile });
            uploadedCallback?.(data);

            return uploadedFile;
        } catch (error) {
            if (sourceRef.current !== activeSource) return;
            if (isCardMedia) {
                setFailedFile(file);
                Toast.Add.error(t("card.Attachment upload failed. Retry the same file or cancel."));
                return;
            }
            const { handle } = setupApiErrorHandler({
                [EHttpStatus.HTTP_404_NOT_FOUND]: {
                    message: t("editor.errors.upload.unknown"),
                    toast: true,
                },
                [EHttpStatus.HTTP_400_BAD_REQUEST]: {
                    message: t("editor.errors.upload.unknown"),
                    toast: true,
                },
                [EHttpStatus.HTTP_500_INTERNAL_SERVER_ERROR]: {
                    message: t("editor.errors.upload.unknown"),
                    toast: true,
                },
            });

            handle(error);
            throw error;
        } finally {
            if (sourceRef.current === activeSource) {
                setProgress(0);
                setIsUploading(false);
                setUploadingFile(undefined);
            }
        }
    }

    return {
        decisionDialog,
        retryUpload: failedFile ? () => uploadThing(failedFile) : undefined,
        isUploading,
        progress,
        uploadFile: uploadThing,
        uploadedFile: uploadedFile?.source === source ? uploadedFile.file : undefined,
        uploadingFile,
    };
}
