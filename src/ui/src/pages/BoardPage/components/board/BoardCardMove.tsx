import { useEffect, useRef, useState, type RefObject } from "react";
import { isCancel } from "axios";
import { useTranslation } from "react-i18next";
import * as Tabs from "@radix-ui/react-tabs";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import Input from "@/components/base/Input";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import { DISABLE_DRAGGING_ATTR } from "@/constants";
import { Project, ProjectCard, ProjectColumn } from "@/core/models";
import { ProjectRole } from "@/core/models/roles";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import useAuthStore from "@/core/stores/AuthStore";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useChangeCardOrder from "@/controllers/api/board/useChangeCardOrder";
import useCrossBoardCard, { type CrossBoardRequest, type CrossBoardResult } from "@/controllers/api/board/useCrossBoardCard";
import useGetProjects from "@/controllers/api/dashboard/useGetProjects";
import CardFamilyMoveConfirmation from "./CardFamilyMoveConfirmation";
import { nextCardOrder } from "./BoardGestureData";

type CardMoveTab = "column" | "move" | "clone";

export default function BoardCardMove({
    card,
    desktop = false,
    disabled = false,
}: {
    card: ProjectCard.TModel;
    desktop?: boolean;
    disabled?: boolean;
}) {
    const [t] = useTranslation();
    const [open, setOpen] = useState<{ actor: string; session: number }>();
    const triggerRef = useRef<HTMLButtonElement>(null);
    const actions = card.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    if (!actor || !hasRoleAction(ProjectRole.EAction.CardUpdate)) return null;
    return (
        <>
            <Button
                ref={triggerRef}
                type="button"
                variant="ghost"
                disabled={disabled}
                className={desktop ? "inline-flex h-7 shrink-0 gap-1 px-2 text-xs" : "size-11 shrink-0 p-0 md:hidden"}
                aria-label={t("board.Move card")}
                title={t("board.Move card")}
                {...{ [DISABLE_DRAGGING_ATTR]: "" }}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                    event.stopPropagation();
                    setOpen({ actor, session });
                }}
            >
                <IconComponent icon="arrow-left-right" size="4" />
                {desktop && <span>{t("board.Move")}</span>}
            </Button>
            {open?.actor === actor && open.session === session && (
                <CardMovePicker key={`${actor}:${session}:${card.uid}`} card={card} triggerRef={triggerRef} onClose={() => setOpen(undefined)} />
            )}
        </>
    );
}

function CardMovePicker({
    card,
    triggerRef,
    onClose,
}: {
    card: ProjectCard.TModel;
    triggerRef: RefObject<HTMLButtonElement | null>;
    onClose: () => void;
}) {
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const [sourceUID] = useState(card.project_uid);
    const source = Project.Model.useModel(sourceUID);
    const columnUID = card.useField("project_column_uid");
    const title = card.useField("title");
    const canTransfer = card.useField("can_cross_board") === true && !card.source_type;
    const actions = card.useField("current_auth_role_actions");
    const { hasRoleAction } = useRoleActionFilter(actions);
    const allowed = hasRoleAction(ProjectRole.EAction.CardUpdate);
    const allowedRef = useRef(allowed);
    allowedRef.current = allowed;
    const columns = ProjectColumn.Model.useModels((column) => column.project_uid === sourceUID, [sourceUID]);
    const [tab, setTab] = useState<CardMoveTab>("column");
    const crossBoard = tab !== "column";
    const [query, setQuery] = useState("");
    const [boardUID, setBoardUID] = useState("");
    const [destinationUID, setDestinationUID] = useState("");
    const [busy, setBusy] = useState(false);
    const busyRef = useRef(false);
    const [error, setError] = useState(false);
    const [exposure, setExposure] = useState<string>();
    const attempt = useRef<CrossBoardRequest | undefined>(undefined);
    const abort = useRef<AbortController | undefined>(undefined);
    const { data, isFetching, isError, refetch } = useGetProjects({ enabled: crossBoard, staleTime: 0 });
    const openResult = (result: CrossBoardResult, destinationCardUID: string, message: string) => {
        if (result.excluded && (result.excluded.member_uids.length || result.excluded.label_uids.length)) {
            Toast.Add.info(
                `${t("board.Excluded mappings")}: ${t("card.Members")} ${result.excluded.member_uids.length}, ` +
                    `${t("card.Labels")} ${result.excluded.label_uids.length}`
            );
        }
        Toast.Add.success(message);
        onClose();
        navigate(`/board/${boardUID}/${destinationCardUID}`);
    };
    const request = useCrossBoardCard(boardUID, (result) => openResult(result, card.uid, t("board.Card moved")));
    const move = useChangeCardOrder({ interceptToast: true, canMove: () => allowedRef.current && card.project_uid === sourceUID });
    useEffect(
        () => () => {
            abort.current?.abort();
        },
        []
    );
    useEffect(() => {
        if (!allowed) {
            abort.current?.abort();
            move.cancel();
            onClose();
        }
    }, [allowed]);
    const targetColumns = tab === "column" ? columns : (data?.columns ?? []).filter((column) => column.project_uid === boardUID);
    const activeColumns = targetColumns.filter((column) => !column.is_archive).sort((a, b) => a.order - b.order);
    const nameCounts = new Map<string, number>();
    for (const column of activeColumns) {
        nameCounts.set(column.name, (nameCounts.get(column.name) ?? 0) + 1);
    }
    const sourceColumn = columns.find((column) => column.uid === columnUID);
    const projects = (data?.projects ?? []).filter(
        (project) => project.uid !== sourceUID && project.title.toLowerCase().includes(query.toLowerCase())
    );
    const locked = busy || !!attempt.current;
    const submit = async () => {
        if (busyRef.current || !allowedRef.current || !activeColumns.some((column) => column.uid === destinationUID)) return;
        if (tab === "column" && destinationUID === columnUID) return;
        if (crossBoard && (!boardUID || !canTransfer || isFetching || isError)) return;
        busyRef.current = true;
        setBusy(true);
        setError(false);
        try {
            if (tab === "column") {
                const cards = ProjectCard.Model.getModels((item) => item.project_uid === sourceUID);
                await move.mutateAsync({
                    project_uid: sourceUID,
                    card_uid: card.uid,
                    parent_uid: destinationUID,
                    order: nextCardOrder(cards, destinationUID),
                });
            } else {
                const job = attempt.current ?? {
                    source_project_uid: sourceUID,
                    source_card_uid: card.uid,
                    destination_column_uid: destinationUID,
                    operation: tab,
                    request_uid: crypto.randomUUID(),
                };
                attempt.current = job;
                const controller = new AbortController();
                abort.current = controller;
                const result = await request({ ...job, confirmation_token: exposure }, controller.signal);
                if (controller.signal.aborted) return;
                if (result.confirmation_required) {
                    setExposure(result.confirmation_token);
                    return;
                }
                if (job.operation === "clone" && result.cardUID) {
                    openResult(result, result.cardUID, t("board.Cross-board action completed."));
                }
                return;
            }
            Toast.Add.success(t("board.Card moved"));
            onClose();
        } catch (error) {
            if (!isCancel(error) && !abort.current?.signal.aborted) setError(true);
        } finally {
            busyRef.current = false;
            setBusy(false);
        }
    };
    return (
        <>
            <CardFamilyMoveConfirmation {...move.confirmation} contentClassName="z-[140]" />
            <Dialog.Root
                open
                onOpenChange={(open) => {
                    if (!open && !busyRef.current) onClose();
                }}
            >
                <Dialog.Content
                    data-workbench-context
                    onCloseAutoFocus={(event) => {
                        event.preventDefault();
                        triggerRef.current?.focus({ preventScroll: true });
                    }}
                    overlayClassName="z-[120]"
                    contentWrapperClassName="items-end md:items-center"
                    className="max-h-[85dvh] overflow-y-auto rounded-t-2xl pb-[max(1.5rem,env(safe-area-inset-bottom))] md:rounded-lg"
                >
                    <Dialog.Header>
                        <Dialog.Title>{t("board.Move card")}</Dialog.Title>
                        <Dialog.Description className="break-words">
                            {title} · {source?.title} / {sourceColumn?.name}
                        </Dialog.Description>
                    </Dialog.Header>
                    {exposure ? (
                        <p className="my-4" role="alert">
                            {t(
                                tab === "clone"
                                    ? "board.Cloning will expose this card to new readers. Continue?"
                                    : "board.Moving will expose this card to new readers. Continue?"
                            )}
                        </p>
                    ) : (
                        <Tabs.Root
                            value={tab}
                            onValueChange={(value) => {
                                if (!locked && (value === "column" || value === "move" || value === "clone")) {
                                    setTab(value);
                                    setDestinationUID("");
                                }
                            }}
                        >
                            <Tabs.List aria-label={t("board.Move card")} className="my-4 flex flex-wrap gap-2">
                                <Tabs.Trigger value="column" asChild>
                                    <Button variant={tab === "column" ? "secondary" : "ghost"} disabled={locked}>
                                        {t("board.Move within board")}
                                    </Button>
                                </Tabs.Trigger>
                                <Tabs.Trigger value="move" asChild>
                                    <Button variant={tab === "move" ? "secondary" : "ghost"} disabled={locked || !canTransfer}>
                                        {t("board.Transfer to another board")}
                                    </Button>
                                </Tabs.Trigger>
                                <Tabs.Trigger value="clone" asChild>
                                    <Button variant={tab === "clone" ? "secondary" : "ghost"} disabled={locked || !canTransfer}>
                                        {t("board.Clone card")}
                                    </Button>
                                </Tabs.Trigger>
                            </Tabs.List>
                            <Tabs.Content value="column" />
                            {crossBoard && (
                                <Tabs.Content value={tab} className="space-y-2">
                                    <Input
                                        aria-label={t("dashboard.Search projects")}
                                        placeholder={t("dashboard.Search projects...")}
                                        value={query}
                                        disabled={locked}
                                        onChange={(event) => {
                                            setQuery(event.target.value);
                                            setBoardUID("");
                                            setDestinationUID("");
                                        }}
                                    />
                                    {isFetching ? (
                                        <p role="status">{t("common.Loading...")}</p>
                                    ) : isError ? (
                                        <Button variant="outline" onClick={() => void refetch()}>
                                            {t("common.Retry")}
                                        </Button>
                                    ) : (
                                        <select
                                            className="h-11 w-full rounded-md border bg-background px-3 text-sm"
                                            value={boardUID}
                                            aria-label={t("board.Destination board")}
                                            disabled={locked}
                                            onChange={(event) => {
                                                setBoardUID(event.target.value);
                                                setDestinationUID("");
                                            }}
                                        >
                                            <option value="">{t("board.Destination board")}</option>
                                            {projects.map((project) => (
                                                <option key={project.uid} value={project.uid}>
                                                    {project.title}
                                                </option>
                                            ))}
                                        </select>
                                    )}
                                    {!isFetching && !isError && !projects.length && <p role="status">{t("dashboard.No projects found")}</p>}
                                </Tabs.Content>
                            )}
                            <div className="my-4 flex max-h-64 flex-col gap-2 overflow-y-auto" aria-label={t("board.Destination column")}>
                                {(tab === "column" || boardUID) && !isFetching && !activeColumns.length && (
                                    <p role="status">{t("board.No columns available")}</p>
                                )}
                                {activeColumns.map((column, index) => (
                                    <Button
                                        key={column.uid}
                                        variant={destinationUID === column.uid ? "secondary" : "outline"}
                                        className="min-h-11 shrink-0 justify-between"
                                        disabled={locked || (tab === "column" && column.uid === columnUID)}
                                        aria-pressed={destinationUID === column.uid}
                                        onClick={() => setDestinationUID(column.uid)}
                                    >
                                        <span className="truncate">
                                            {(nameCounts.get(column.name) ?? 0) > 1
                                                ? t("board.Column {{position}} · {{name}}", { position: index + 1, name: column.name })
                                                : column.name}
                                        </span>
                                        {tab === "column" && column.uid === columnUID && <IconComponent icon="check" size="4" />}
                                    </Button>
                                ))}
                            </div>
                        </Tabs.Root>
                    )}
                    {error && (
                        <p role="alert" className="my-3 text-sm text-destructive">
                            {t("board.Cross-board action failed. Retry or cancel.")}
                        </p>
                    )}
                    <Dialog.Footer>
                        <Button variant="outline" disabled={busy} onClick={onClose}>
                            {t("common.Cancel")}
                        </Button>
                        <Button disabled={busy || !destinationUID || (crossBoard && (isFetching || isError))} onClick={() => void submit()}>
                            {exposure ? t("board.Confirm exposure") : t(tab === "clone" ? "board.Clone card" : "board.Move card")}
                        </Button>
                    </Dialog.Footer>
                </Dialog.Content>
            </Dialog.Root>
        </>
    );
}
