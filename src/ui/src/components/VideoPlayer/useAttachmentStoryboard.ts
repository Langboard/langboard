import { useEffect, useState } from "react";
import { api } from "@/core/helpers/Api";
import { ProjectCardAttachment } from "@/core/models";
import { useEditorData } from "@/core/providers/EditorDataProvider";
import useAuthStore, { getAuthStore } from "@/core/stores/AuthStore";
import { protectedMediaPath } from "@/core/hooks/useProtectedMedia";
import { type AttachmentStoryboard, createStoryboardResource } from "./createStoryboardResource";

/** Match only the current card's native attachment; server authority remains mandatory. */
export default function useAttachmentStoryboard(src: string | undefined) {
    const form = useEditorData().form;
    const projectUID: string = form?.project_uid ?? "";
    const cardUID: string = form?.card_uid ?? "";
    const path = protectedMediaPath(src);
    const attachments = ProjectCardAttachment.Model.useModels((item) => item.card_uid === cardUID, [cardUID]);
    const matches = path ? attachments.filter((item) => protectedMediaPath(item.url) === path) : [];
    const attachmentUID = matches.length === 1 ? matches[0].uid : "";
    const key =
        projectUID && cardUID && attachmentUID
            ? ["", "board", projectUID, "card", cardUID, "attachment", attachmentUID, "storyboard"].map(encodeURIComponent).join("/")
            : "";
    const session = useAuthStore((store) => store.getSessionVersion());
    const [loaded, setLoaded] = useState<{ key: string; session: number; url: string }>();
    useEffect(() => {
        if (!key) return;
        const controller = new AbortController();
        let disposed = false;
        let resource: ReturnType<typeof createStoryboardResource> | undefined;
        let url: string | undefined;
        api.get<AttachmentStoryboard>(key, { signal: controller.signal, env: { interceptToast: false } as never })
            .then(({ data }) => {
                if (disposed || session !== getAuthStore().getSessionVersion()) return;
                resource = createStoryboardResource(data);
                url = URL.createObjectURL(resource.blob);
                setLoaded({ key, session, url });
            })
            .catch(() => {
                if (!disposed) setLoaded(undefined);
            });
        return () => {
            disposed = true;
            controller.abort();
            if (url) URL.revokeObjectURL(url);
            resource?.dispose();
        };
    }, [key, session]);
    return key && loaded?.key === key && loaded.session === session ? loaded.url : undefined;
}
