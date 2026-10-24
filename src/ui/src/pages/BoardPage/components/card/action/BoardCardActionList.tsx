import BoardCardActionAddDecision from "./decision/BoardCardActionAddDecision";
import Skeleton from "@/components/base/Skeleton";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import { lazy, memo, Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import IconComponent from "@/components/base/IconComponent";
import BoardCardActionActivity from "@/pages/BoardPage/components/card/action/BoardCardActionActivity";
import BoardCardActionShare from "@/pages/BoardPage/components/card/action/BoardCardActionShare";
import BoardCardActionRelationship from "@/pages/BoardPage/components/card/action/relationship/BoardCardActionRelationship";
import BoardCardActionAddChecklist from "@/pages/BoardPage/components/card/action/checklist/BoardCardActionAddChecklist";
import BoardCardActionArchive from "@/pages/BoardPage/components/card/action/BoardCardActionArchive";
import BoardCardActionDelete from "@/pages/BoardPage/components/card/action/BoardCardActionDelete";
import BoardCardActionMetadata from "@/pages/BoardPage/components/card/action/BoardCardActionMetadata";
import BoardCardActionBotScope from "@/pages/BoardPage/components/card/action/botScope/BoardCardActionBotScope";
import { ProjectRole } from "@/core/models/roles";
import BoardCardActionAttachFile from "@/pages/BoardPage/components/card/action/file/BoardCardActionAttachFile";
import CardWorkbenchLenses, { CARD_LENS_SECTIONS, CardWorkbenchLens } from "../CardWorkbenchLenses";
import useGetCardDetails from "@/controllers/api/card/useGetCardDetails";

function BoardCardContextAction({ lens }: { lens: CardWorkbenchLens }) {
    const { projectUID, card, isCardEditing } = useBoardCard();
    const [t] = useTranslation();
    const [open, setOpen] = useState(false);
    const title = t(`card.lenses.${lens}`);
    const { data, isFetching, error, refetch } = useGetCardDetails(
        { project_uid: projectUID, card_uid: card.uid, sections: CARD_LENS_SECTIONS[lens] },
        { enabled: open }
    );
    return (
        <Dialog.Root open={open} onOpenChange={setOpen}>
            <Dialog.Trigger asChild>
                <Button variant="secondary" className={sharedButtonClassName} disabled={isCardEditing}>
                    <IconComponent icon={lens === "history" ? "history" : lens === "review" ? "clipboard-check" : "play"} size="4" />
                    {title}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto">
                <Dialog.Title>{title}</Dialog.Title>
                <Dialog.Description className="sr-only">{t("card.lenses.Views")}</Dialog.Description>
                {open &&
                    (error ? (
                        <div role="alert">
                            <p>{t("card.Could not load card")}</p>
                            <Button onClick={() => void refetch()} disabled={isFetching}>
                                {t("common.Retry")}
                            </Button>
                        </div>
                    ) : !data ? (
                        <p role="status">{t("common.Loading...")}</p>
                    ) : (
                        <>
                            {lens === "execution" && (
                                <Button variant="outline" onClick={() => void refetch()} disabled={isFetching}>
                                    {t("card.lenses.Refresh execution")}
                                </Button>
                            )}
                            <CardWorkbenchLenses
                                card={data.card}
                                lens={lens}
                                receipts={data.execution_receipts}
                                agentExecution={isFetching ? undefined : data.agent_execution}
                            />
                        </>
                    ))}
            </Dialog.Content>
        </Dialog.Root>
    );
}

const CardSignalEvidence = lazy(() => import("../CardSignalEvidence"));
const CardAppResources = lazy(() => import("../CardAppResources"));
const CardAppInteractions = lazy(() => import("../CardAppInteractions"));
const CardAppRuntimes = lazy(() => import("../CardAppRuntimes"));

function BoardCardActionManager({ kind }: { kind: "signals" | "resources" | "interactions" | "runtimes" }) {
    const { projectUID, card, currentUser, canEditCard, hasRoleAction, socket } = useBoardCard();
    const [t] = useTranslation();
    const client = useQueryClient();
    const [open, setOpen] = useState(false);
    const visibility = card.useField("visibility");
    const cardRevision = card.useField("last_change_seq");
    const locale = { signals: "card.signals", resources: "card.appResources", interactions: "card.appInteractions", runtimes: "card.appRuntimes" }[
        kind
    ];
    const title = t(`${locale}.title`);
    const archivedAt = card.useField("archived_at");
    if ((kind === "signals" && visibility === "PRIVATE") || (kind === "resources" && !hasRoleAction(ProjectRole.EAction.Update))) return null;
    return (
        <Dialog.Root open={open} onOpenChange={setOpen}>
            <Dialog.Trigger asChild>
                <Button variant="secondary" className={sharedButtonClassName}>
                    <IconComponent icon={kind === "signals" ? "activity" : "plug"} size="4" />
                    {title}
                </Button>
            </Dialog.Trigger>
            <Dialog.Content className="max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-xl overflow-y-auto">
                <Dialog.Title>{title}</Dialog.Title>
                <Dialog.Description className="sr-only">{t(`${locale}.${kind === "signals" ? "hint" : "help"}`)}</Dialog.Description>
                {open && (
                    <Suspense fallback={<p role="status">{t("common.Loading...")}</p>}>
                        {kind === "signals" ? (
                            <CardSignalEvidence
                                embedded
                                projectUID={projectUID}
                                cardUID={card.uid}
                                socket={socket}
                                cardRevision={cardRevision}
                                canEdit={canEditCard}
                                onChanged={() => {
                                    void client.invalidateQueries({ queryKey: [`get-card-details-${projectUID}-${card.uid}`] });
                                }}
                            />
                        ) : kind === "resources" ? (
                            <CardAppResources embedded projectUID={projectUID} cardUID={card.uid} userUID={currentUser.uid} />
                        ) : kind === "interactions" ? (
                            <CardAppInteractions
                                embedded
                                projectUID={projectUID}
                                cardUID={card.uid}
                                userUID={currentUser.uid}
                                canRespond={canEditCard && !archivedAt}
                            />
                        ) : (
                            <CardAppRuntimes
                                embedded
                                projectUID={projectUID}
                                cardUID={card.uid}
                                userUID={currentUser.uid}
                                canRespond={canEditCard && !archivedAt}
                            />
                        )}
                    </Suspense>
                )}
            </Dialog.Content>
        </Dialog.Root>
    );
}

const sharedButtonClassName = "mb-2 w-full justify-start gap-2 rounded-none px-2 py-1 sm:h-7";

export function SkeletonBoardCardActionList() {
    return (
        <>
            <Skeleton className={sharedButtonClassName} />
            <Skeleton className={sharedButtonClassName} />
            <Skeleton className={sharedButtonClassName} />
            <Skeleton className={sharedButtonClassName} />
            <Skeleton className={sharedButtonClassName} />
            <Skeleton className={sharedButtonClassName} />
        </>
    );
}

const BoardCardActionList = memo(() => {
    const { projectUID, card, currentUser, hasRoleAction } = useBoardCard();
    const isAdmin = currentUser.useField("is_admin");
    const archivedAt = card.useField("archived_at");
    const visibility = card.useField("visibility");
    const canDelete = card.useField("can_delete");

    return (
        <>
            {(["execution", "review", "history"] as const).map((lens) => (
                <BoardCardContextAction key={`${currentUser.uid}/${projectUID}/${card.uid}/${lens}`} lens={lens} />
            ))}
            {visibility !== "PRIVATE" && <BoardCardActionBotScope buttonClassName={sharedButtonClassName} />}
            <BoardCardActionRelationship buttonClassName={`${sharedButtonClassName} sm:hidden`} />
            <BoardCardActionAttachFile buttonClassName={sharedButtonClassName} />
            <BoardCardActionAddChecklist buttonClassName={sharedButtonClassName} />
            <BoardCardActionAddDecision buttonClassName={sharedButtonClassName} />
            <BoardCardActionMetadata buttonClassName={sharedButtonClassName} />
            <BoardCardActionActivity buttonClassName={sharedButtonClassName} />
            <BoardCardActionManager key={`${currentUser.uid}/${projectUID}/${card.uid}/signals`} kind="signals" />
            <BoardCardActionManager key={`${currentUser.uid}/${projectUID}/${card.uid}/resources`} kind="resources" />
            <BoardCardActionManager key={`${currentUser.uid}/${projectUID}/${card.uid}/interactions`} kind="interactions" />
            <BoardCardActionManager key={`${currentUser.uid}/${projectUID}/${card.uid}/runtimes`} kind="runtimes" />
            {visibility !== "PRIVATE" && <BoardCardActionShare buttonClassName={sharedButtonClassName} />}
            {!archivedAt && (hasRoleAction(ProjectRole.EAction.CardUpdate) || isAdmin) ? (
                <BoardCardActionArchive buttonClassName={sharedButtonClassName} />
            ) : null}
            {!!archivedAt && canDelete && (hasRoleAction(ProjectRole.EAction.CardDelete) || isAdmin) ? (
                <BoardCardActionDelete buttonClassName={sharedButtonClassName} />
            ) : null}
        </>
    );
});

export default BoardCardActionList;
