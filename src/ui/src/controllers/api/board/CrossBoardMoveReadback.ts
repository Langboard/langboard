import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { ProjectCard, ProjectColumn, ProjectChecklist, GlobalRelationshipType } from "@/core/models";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";

export interface BoardCardsData {
    cards: ProjectCard.IStore[];
    columns: ProjectColumn.Interface[];
    checklists: ProjectChecklist.IStore[];
    global_relationships: GlobalRelationshipType.Interface[];
}

/** Validate both ordinary snapshots and destination proof before touching shared models. */
export function validateBoardCardsResponse(data: BoardCardsData, projectUID: string) {
    if (!data || ![data.cards, data.columns, data.checklists, data.global_relationships].every(Array.isArray)) {
        throw new Error("Incomplete board cards response");
    }
    if (
        [...data.cards, ...data.columns, ...data.checklists, ...data.global_relationships].some(
            (model) => !model || typeof model.uid !== "string" || !model.uid
        )
    ) {
        throw new Error("Invalid board cards response");
    }
    const responseCardUIDs = new Set<string>(data.cards.map((card: ProjectCard.Interface) => card.uid));
    if (
        data.columns.some((column: ProjectColumn.Interface) => !column || column.project_uid !== projectUID) ||
        data.cards.some(
            (card: ProjectCard.IStore) =>
                !card ||
                card.project_uid !== projectUID ||
                card.labels?.some((label) => label.project_uid !== projectUID) ||
                card.relationships?.some((relationship) => relationship.parent_card_uid !== card.uid && relationship.child_card_uid !== card.uid)
        ) ||
        data.checklists.some(
            (checklist: ProjectChecklist.IStore) =>
                !checklist ||
                !responseCardUIDs.has(checklist.card_uid) ||
                checklist.checkitems?.some(
                    (item) =>
                        item.checklist_uid !== checklist.uid ||
                        item.card_uid !== checklist.card_uid ||
                        (item.cardified_card && item.cardified_card.project_uid !== projectUID)
                )
        )
    ) {
        throw new Error("Board cards response belongs to another board");
    }
}

interface MoveReadback {
    source: ProjectCard.TModel;
    sourceProjectUID: string;
    destinationProjectUID: string;
    requestUID: string;
    actorUID: string;
    session: number;
    signal: AbortSignal;
}
const pending = new Set<MoveReadback>();

export function registerMoveReadback(move: MoveReadback) {
    for (const previous of pending) {
        if (previous.source === move.source) pending.delete(previous);
    }
    pending.add(move);
    const cleanup = () => {
        pending.delete(move);
        move.signal.removeEventListener("abort", cleanup);
    };
    move.signal.addEventListener("abort", cleanup, { once: true });
    return cleanup;
}

/** An omitted source survives only with a fresh, authorized destination snapshot. */
export async function readbackOmittedMoves(projectUID: string, omittedUIDs: Set<string>, signal: AbortSignal, assertCurrent: () => void) {
    await Promise.all(
        [...pending]
            .filter((move) => move.sourceProjectUID === projectUID && omittedUIDs.has(move.source.uid))
            .map(async (move) => {
                const current = () => {
                    const auth = useAuthStore.getState();
                    return (
                        !signal.aborted &&
                        !move.signal.aborted &&
                        pending.has(move) &&
                        auth.currentUser?.uid === move.actorUID &&
                        auth.getSessionVersion() === move.session &&
                        ProjectCard.Model.getModel(move.source.uid) === move.source &&
                        move.source.project_uid === projectUID &&
                        move.source.can_cross_board === true &&
                        !move.source.source_type
                    );
                };
                if (!current()) return;
                try {
                    const response = await api.get<BoardCardsData>(
                        Utils.String.format(Routing.API.BOARD.GET_CARDS, { uid: move.destinationProjectUID }),
                        {
                            signal: AbortSignal.any([signal, move.signal]),
                            timeout: 15_000,
                            params: { include_automation: false },
                            env: { interceptToast: true } as never,
                        }
                    );
                    if (response.status !== 200) return;
                    validateBoardCardsResponse(response.data, move.destinationProjectUID);
                    const card = response.data.cards.find((candidate) => candidate.uid === move.source.uid);
                    if (
                        !card ||
                        card.source_type ||
                        card.can_cross_board !== true ||
                        !response.data.columns.some((column) => column.uid === card.project_column_uid) ||
                        !current()
                    )
                        return;
                    assertCurrent();
                    ProjectCard.Model.fromOne(card, true);
                } catch {
                    // Failed or revoked destination proof leaves ordinary source omission deletion intact.
                }
            })
    );
}
