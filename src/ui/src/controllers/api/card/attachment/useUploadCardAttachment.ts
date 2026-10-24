import { Routing } from "@langboard/core/constants";
import { useNativeAttachmentUpload } from "@/components/AttachmentDuplicateDecision";
import { ProjectCardAttachment } from "@/core/models";
import { TMutationOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Utils } from "@langboard/core/utils";
import { AxiosProgressEvent } from "axios";

export interface IUploadCardAttachmentForm {
    project_uid: string;
    card_uid: string;
    attachment: File;
    onUploadProgress?: (progressEvent: AxiosProgressEvent) => void;
}

const useUploadCardAttachment = (options?: TMutationOptions<IUploadCardAttachmentForm>, source = "") => {
    const { upload, decisionDialog } = useNativeAttachmentUpload(source);
    const { mutate } = useQueryMutation();

    const updateCardAttachment = async (params: IUploadCardAttachmentForm) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.ATTACHMENT.UPLOAD, {
            uid: params.project_uid,
            card_uid: params.card_uid,
        });
        const result = await upload(url, params.card_uid, params.attachment, params.onUploadProgress);
        if (!result) return;
        ProjectCardAttachment.Model.fromOne(result, true);
        return result;
    };

    const result = mutate(["upload-card-attachment"], updateCardAttachment, {
        ...options,
        retry: 0,
    });

    return { ...result, decisionDialog };
};

export default useUploadCardAttachment;
