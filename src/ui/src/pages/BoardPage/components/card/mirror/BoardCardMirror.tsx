import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import { PlateEditor } from "@/components/Editor/plate-editor";
import useArchiveCard from "@/controllers/api/card/useArchiveCard";
import { createApiErrorMessageHandler } from "@/core/helpers/setupApiErrorHandler";
import { deleteCardModel } from "@/core/helpers/ModelHelper";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { ROUTES } from "@/core/routing/constants";
import { useTranslation } from "react-i18next";
import type { Interface } from "@/core/models/ProjectCard";

export function getMirrorOriginalPath(resource: Interface["linked_resource"]): string | undefined {
    if (resource?.status !== "available" || !resource.allowed_actions?.includes("open_original") || !resource.project_uid || !resource.uid) return;
    const path = ROUTES.BOARD.CARD(resource.project_uid, resource.uid);
    return resource.url === path ? path : undefined;
}

export function BoardCardMirrorSurface({
    resource,
    columnName,
    children,
    onOpenOriginal,
    onRemove,
    isRemoving,
}: {
    resource: Interface["linked_resource"];
    columnName?: string;
    children?: React.ReactNode;
    onOpenOriginal: (path: string) => void;
    onRemove?: () => void;
    isRemoving?: boolean;
}) {
    const [t] = useTranslation();
    const { currentUser } = useBoardCard();
    const available = resource?.status === "available";
    const title = available ? resource.title : t("wiki.Source unavailable");
    const path = getMirrorOriginalPath(resource);
    return (
        <Flex direction="col" className="h-full min-h-0 gap-3" data-card-mirror="" data-card-surface="">
            <Dialog.Header className="relative shrink-0 border-b pb-3 text-left">
                <Dialog.Title className="break-words pr-24 text-xl font-semibold">{title}</Dialog.Title>
                <Dialog.Description>{columnName}</Dialog.Description>
                {children}
            </Dialog.Header>
            <Box className="min-h-0 flex-1 overflow-y-auto">
                {available && resource.content ? (
                    <PlateEditor
                        value={resource.content}
                        readOnly
                        editorType="view"
                        form={{ project_uid: resource.project_uid ?? "" }}
                        currentUser={currentUser}
                        mentionables={[]}
                        className="px-0 py-4"
                    />
                ) : (
                    <p className="whitespace-pre-wrap break-words py-4 text-sm text-muted-foreground">
                        {available ? resource.preview : t("wiki.Source unavailable")}
                    </p>
                )}
            </Box>
            <Flex items="center" justify="between" gap="2" wrap>
                <Button variant="secondary" disabled={!path} onClick={() => path && onOpenOriginal(path)}>
                    <IconComponent icon="external-link" size="4" />
                    {t("wiki.Open original")}
                </Button>
                {onRemove && (
                    <Button variant="destructive" disabled={isRemoving} onClick={onRemove}>
                        <IconComponent icon="unlink" size="4" />
                        {t("wiki.Remove from board")}
                    </Button>
                )}
            </Flex>
        </Flex>
    );
}

export default function BoardCardMirror({
    isExpanded,
    setIsExpanded,
    onClose,
}: {
    isExpanded: boolean;
    setIsExpanded?: React.Dispatch<React.SetStateAction<boolean>>;
    onClose?: () => void;
}) {
    const { card, projectUID, canEditCard } = useBoardCard();
    const resource = card.useField("linked_resource");
    const columnName = card.useField("project_column_name");
    const canDelete = card.useField("can_delete");
    const navigate = usePageNavigateRef();
    const [t] = useTranslation();
    const { mutateAsync: archiveCard, isPending } = useArchiveCard({ interceptToast: true });
    const remove = async () => {
        if (isPending || !canEditCard || !canDelete) return;
        try {
            await archiveCard({ project_uid: projectUID, card_uid: card.uid });
            deleteCardModel(card.uid, true);
            Toast.Add.success(t("wiki.Linked card removed"));
            navigate(ROUTES.BOARD.MAIN(projectUID), { replace: true });
        } catch (error) {
            Toast.Add.error(createApiErrorMessageHandler()(error));
        }
    };
    return (
        <Box className="h-full min-h-0 overflow-hidden rounded-2xl border bg-background px-4 py-4 shadow-2xl sm:px-6 sm:py-6">
            <BoardCardMirrorSurface
                resource={resource}
                columnName={columnName}
                onOpenOriginal={navigate}
                onRemove={canEditCard && canDelete ? remove : undefined}
                isRemoving={isPending}
            >
                <Flex gap="1" className="absolute right-0 top-0">
                    {setIsExpanded && (
                        <Button
                            variant="ghost"
                            size="icon"
                            title={t(isExpanded ? "common.Collapse" : "common.Expand")}
                            onClick={() => setIsExpanded((value) => !value)}
                        >
                            <IconComponent icon={isExpanded ? "minimize-2" : "maximize-2"} size="4" />
                        </Button>
                    )}
                    <Button variant="ghost" size="icon" title={t("common.Close")} onClick={onClose}>
                        <IconComponent icon="x" size="4" />
                    </Button>
                </Flex>
            </BoardCardMirrorSurface>
        </Box>
    );
}
