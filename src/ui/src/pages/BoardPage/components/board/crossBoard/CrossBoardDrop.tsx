import { ProjectRole } from "@/core/models/roles";
import { useEffect, useRef, useState } from "react";
import { dropTargetForElements } from "@atlaskit/pragmatic-drag-and-drop/element/adapter";
import { ProjectCard, ProjectColumn } from "@/core/models";
import { BOARD_DND_SYMBOL_SET } from "../BoardConstants";
import { useBoardSplitContext } from "@/core/providers/BoardSplitProvider";
import { useBoard } from "@/core/providers/BoardProvider";
import useAuthStore from "@/core/stores/AuthStore";
import useCrossBoardCard, { type CrossBoardRequest, type CrossBoardResult } from "@/controllers/api/board/useCrossBoardCard";
import Dialog from "@/components/base/Dialog";
import Button from "@/components/base/Button";
import { useTranslation } from "react-i18next";

export function isCrossBoardSource(card: ProjectCard.TModel) {
    return !card.source_type && card.can_cross_board === true;
}

export default function CrossBoardDrop({ column, children }: { column: ProjectColumn.TModel; children: React.ReactNode }) {
    const [t] = useTranslation();
    const { hasRoleAction } = useBoard();
    const split = useBoardSplitContext();
    const target = useRef<HTMLDivElement>(null);
    const [pending, setPending] = useState<CrossBoardRequest>();
    const pendingRef = useRef(pending);
    pendingRef.current = pending;
    const [result, setResult] = useState<CrossBoardResult>();
    const [error, setError] = useState(false);
    const [busy, setBusy] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const busyRef = useRef(false);
    const [over, setOver] = useState(false);
    const abort = useRef<AbortController | undefined>(undefined);
    const request = useCrossBoardCard(column.project_uid);
    const actor = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const allowed = split?.panes.length === 2 && hasRoleAction(ProjectRole.EAction.CardWrite);
    useEffect(() => {
        setPending(undefined);
        setResult(undefined);
        setError(false);
        setAttempted(false);
        busyRef.current = false;
        setBusy(false);
        abort.current?.abort();
        abort.current = undefined;
        return () => abort.current?.abort();
    }, [column, actor, session, allowed]);
    useEffect(() => {
        if (!pending) return;
        const invalidate = () => {
            const source = ProjectCard.Model.getModel(pending.source_card_uid);
            if (
                source &&
                isCrossBoardSource(source) &&
                (source.project_uid === pending.source_project_uid ||
                    (pendingRef.current?.operation === "move" && source.project_uid === column.project_uid))
            )
                return;
            abort.current?.abort();
            setPending(undefined);
            setResult(undefined);
            setError(false);
        };
        const source = ProjectCard.Model.getModel(pending.source_card_uid);
        const stopFields = source?.subscribeFields(["can_cross_board", "project_uid", "source_type"], invalidate);
        const stopDeletion = ProjectCard.Model.subscribe("DELETION", `cross-board-${pending.request_uid}`, (uids) => {
            if (uids.includes(pending.source_card_uid)) invalidate();
        });
        return () => {
            stopFields?.();
            stopDeletion();
        };
    }, [pending?.source_card_uid, pending?.request_uid, column.project_uid]);
    useEffect(() => {
        if (!target.current || !allowed) return;
        return dropTargetForElements({
            element: target.current,
            canDrop: ({ source }) => {
                const row = source.data.row;
                return (
                    window.innerWidth >= 768 &&
                    !pendingRef.current &&
                    !busyRef.current &&
                    BOARD_DND_SYMBOL_SET.row in source.data &&
                    row instanceof ProjectCard.Model &&
                    row.project_uid !== column.project_uid &&
                    isCrossBoardSource(row)
                );
            },
            getData: () => ({ crossBoardColumnUID: column.uid }),
            onDragEnter: () => setOver(true),
            onDragLeave: () => setOver(false),
            onDrop: ({ source }) => {
                setOver(false);
                const row = source.data.row as ProjectCard.TModel;
                setResult(undefined);
                setError(false);
                setAttempted(false);
                setPending({
                    source_project_uid: row.project_uid,
                    source_card_uid: row.uid,
                    destination_column_uid: column.uid,
                    operation: "link",
                    request_uid: crypto.randomUUID(),
                });
            },
        });
    }, [allowed, column]);
    const execute = async (operation: CrossBoardRequest["operation"], confirmationToken?: string) => {
        if (!pending || busyRef.current || !allowed) return;
        if (attempted && operation !== pending.operation) return;
        const source = ProjectCard.Model.getModel(pending.source_card_uid);
        const replayAtDestination = attempted && operation === "move" && source?.project_uid === column.project_uid;
        if (!source || (!replayAtDestination && source.project_uid !== pending.source_project_uid) || !isCrossBoardSource(source)) {
            setError(true);
            return;
        }
        busyRef.current = true;
        setBusy(true);
        setError(false);
        setAttempted(true);
        const controller = new AbortController();
        abort.current = controller;
        const next = { ...pending, operation, confirmation_token: confirmationToken };
        setPending(next);
        try {
            const response = await request(next, controller.signal);
            if (!controller.signal.aborted) {
                setResult(response);
                if (!response.confirmation_required) setPending(undefined);
            }
        } catch {
            if (!controller.signal.aborted) setError(true);
        } finally {
            if (abort.current === controller) {
                busyRef.current = false;
                setBusy(false);
            }
        }
    };
    return (
        <div ref={target} className={over ? "flex shrink-0 rounded-2xl ring-2 ring-primary" : "flex shrink-0"} data-cross-board-column={column.uid}>
            {children}
            <Dialog.Root
                open={!!pending || !!result}
                onOpenChange={(open) => {
                    if (!open && !busy) {
                        setPending(undefined);
                        setResult(undefined);
                    }
                }}
            >
                <Dialog.Content>
                    <Dialog.Header>
                        <Dialog.Title>{t("board.Cross-board card")}</Dialog.Title>
                        <Dialog.Description>{t("board.Link a read-only mirror, create an independent clone, or move this card.")}</Dialog.Description>
                    </Dialog.Header>
                    {error && <p role="alert">{t("board.Cross-board action failed. Retry or cancel.")}</p>}
                    {result?.confirmation_required ? (
                        <>
                            <p>
                                {t(
                                    pending?.operation === "move"
                                        ? "board.Moving will expose this card to new readers. Continue?"
                                        : "board.Cloning will expose this card to new readers. Continue?"
                                )}
                            </p>
                            <Button disabled={busy} onClick={() => pending && void execute(pending.operation, result.confirmation_token)}>
                                {t("board.Confirm exposure")}
                            </Button>
                        </>
                    ) : pending ? (
                        <Dialog.Footer>
                            {(!attempted || pending.operation === "link") && (
                                <Button disabled={busy} onClick={() => void execute("link", pending.confirmation_token)}>
                                    {t("board.Link card")}
                                </Button>
                            )}
                            {(!attempted || pending.operation === "clone") && (
                                <Button disabled={busy} onClick={() => void execute("clone", pending.confirmation_token)}>
                                    {t("board.Clone card")}
                                </Button>
                            )}
                            {(!attempted || pending.operation === "move") && (
                                <Button disabled={busy} onClick={() => void execute("move", pending.confirmation_token)}>
                                    {t("board.Move card")}
                                </Button>
                            )}
                        </Dialog.Footer>
                    ) : (
                        <p role="status">{t("board.Cross-board action completed.")}</p>
                    )}
                    {result?.excluded && (
                        <div role="status">
                            <p>{t("board.Excluded mappings")}</p>
                            <p>
                                {t("board.Members")}: {result.excluded.member_uids.length}
                            </p>
                            <p>
                                {t("board.Labels")}: {result.excluded.label_uids.length}
                            </p>
                        </div>
                    )}
                    <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => {
                            setPending(undefined);
                            setResult(undefined);
                        }}
                    >
                        {t("common.Cancel")}
                    </Button>
                </Dialog.Content>
            </Dialog.Root>
        </div>
    );
}
