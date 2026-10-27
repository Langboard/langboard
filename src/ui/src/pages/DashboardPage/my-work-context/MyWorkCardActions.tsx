import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import useGetCardDetails, { type IGetCardDetailsResponse } from "@/controllers/api/card/useGetCardDetails";
import useChangeCardDetails from "@/controllers/api/card/useChangeCardDetails";
import useSetCardCompleted from "@/controllers/api/board/useSetCardCompleted";
import useAuthStore from "@/core/stores/AuthStore";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { ProjectRole } from "@/core/models/roles";
import type { ProjectCard } from "@/core/models";
import { InlineDeadlineField } from "@/pages/BoardPage/components/card/BoardCardInlineDeadline";

const pendingWrites = new Set<string>();

// The detail projection derives this flag from ownership and linked-resource policy.
const isNativeCard = (card: ProjectCard.TModel) => card.can_cross_board === true && !card.source_type && !card.linked_resource;

export default function MyWorkCardActions({ projectUID, cardUID }: { projectUID: string; cardUID: string }) {
    const [t] = useTranslation();
    const [opened, setOpened] = useState(false);
    const [requested, setRequested] = useState(false);
    return (
        <div className="min-w-0 px-3 pb-2">
            <Button
                size="sm"
                variant="outline"
                aria-expanded={opened}
                onClick={() => {
                    setRequested(true);
                    setOpened(!opened);
                }}
            >
                {t("dashboard.Card actions")}
            </Button>
            {requested && (
                <div hidden={!opened}>
                    <Actions projectUID={projectUID} cardUID={cardUID} />
                </div>
            )}
        </div>
    );
}

function Actions({ projectUID, cardUID }: { projectUID: string; cardUID: string }) {
    const [t] = useTranslation();
    const client = useQueryClient();
    const [pending, setPending] = useState(false);
    const [failed, setFailed] = useState(false);
    const busy = useRef(false);
    const mounted = useRef(true);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const identity = useAuthStore((state) => state.getIdentityVersion());
    const session = useAuthStore((state) => state.getSessionVersion());
    const detail = useGetCardDetails(
        { project_uid: projectUID, card_uid: cardUID, sections: ["checklists"] },
        { interceptToast: true, staleTime: 0, refetchOnMount: "always" }
    );
    const { mutateAsync: changeDetails } = useChangeCardDetails({ interceptToast: true });
    const { mutateAsync: complete } = useSetCardCompleted({ interceptToast: true });
    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);
    const current = () =>
        mounted.current &&
        !!actor &&
        useAuthStore.getState().currentUser?.uid === actor &&
        useAuthStore.getState().getSessionVersion() === session &&
        useAuthStore.getState().getIdentityVersion() === identity;
    const save = async (deadline?: Date | "") => {
        const writeKey = `${actor}:${identity}:${session}:${projectUID}:${cardUID}`;
        if (busy.current || pendingWrites.has(writeKey) || !current()) throw new Error("Card action is pending or context changed");
        pendingWrites.add(writeKey);
        busy.current = true;
        setPending(true);
        setFailed(false);
        try {
            const fresh = await detail.refetch({ throwOnError: true });
            const card = fresh.data?.card;
            if (!current()) throw new Error("Card action context changed");
            const authority = fresh.data?.action_authority;
            const actions = authority?.role_actions;
            if (
                !card ||
                !isNativeCard(card) ||
                authority?.native_card !== true ||
                !(actions?.includes("*") || actions?.includes(ProjectRole.EAction.CardUpdate)) ||
                (deadline === undefined && (authority?.is_check_card !== true || typeof authority.completed !== "boolean"))
            ) {
                throw new Error("Card action is no longer authorized");
            }
            if (deadline === undefined) await complete({ project_uid: projectUID, card_uid: cardUID, completed: !authority?.completed });
            else await changeDetails({ project_uid: projectUID, card_uid: cardUID, deadline_at: deadline });
            if (!current()) throw new Error("Card action context changed");
            // Deadline changes may also change check-card eligibility. Reread both projections.
            await Promise.all([detail.refetch({ throwOnError: true }), client.resetQueries({ queryKey: ["dashboard-my-work", actor] })]);
        } catch {
            if (current()) setFailed(true);
            throw new Error("Card action failed; reload current permissions before retrying");
        } finally {
            pendingWrites.delete(writeKey);
            busy.current = false;
            if (current()) setPending(false);
        }
    };
    return (
        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2" aria-busy={pending || detail.isFetching}>
            {failed || detail.isError ? (
                <div role="alert">
                    <span>{t("dashboard.Card action failed")}</span>
                    <Button
                        size="sm"
                        variant="outline"
                        disabled={pending || detail.isFetching}
                        onClick={() => {
                            setFailed(false);
                            void detail.refetch();
                        }}
                    >
                        {t("dashboard.Retry")}
                    </Button>
                </div>
            ) : null}
            {detail.data && !detail.isError && !failed && (
                <AuthorizedActions
                    authority={detail.data.action_authority}
                    card={detail.data.card}
                    disabled={pending || detail.isFetching || !current()}
                    save={save}
                />
            )}
            {detail.isFetching && <span role="status">{t("dashboard.Loading work")}</span>}
        </div>
    );
}

function AuthorizedActions({
    card,
    authority,
    disabled,
    save,
}: {
    card: ProjectCard.TModel;
    authority: IGetCardDetailsResponse["action_authority"];
    disabled: boolean;
    save: (deadline?: Date | "") => Promise<void>;
}) {
    const [t] = useTranslation();
    const actions = card.useField("current_auth_role_actions");
    const deadline = card.useField("deadline_at");
    const check = card.useField("is_check_card");
    const completed = card.useField("completed");
    const native = card.useField("can_cross_board");
    const source = card.useField("source_type");
    const linked = card.useField("linked_resource");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const allowed =
        authority?.native_card === true &&
        (authority.role_actions.includes("*") || authority.role_actions.includes(ProjectRole.EAction.CardUpdate)) &&
        native === true &&
        !source &&
        !linked &&
        hasRoleAction(ProjectRole.EAction.CardUpdate);
    if (!allowed) return <span role="status">{t("dashboard.Card actions unavailable")}</span>;
    return (
        <>
            <InlineDeadlineField deadline={deadline} canEdit={!disabled} save={(value) => save(value)} />
            {check === true && authority?.is_check_card === true && typeof authority.completed === "boolean" && (
                <Button
                    size="sm"
                    variant="outline"
                    disabled={disabled}
                    onClick={() => {
                        void save().catch(() => {});
                    }}
                >
                    {t(completed ? "dashboard.Reopen check card" : "dashboard.Complete check card")}
                </Button>
            )}
        </>
    );
}
