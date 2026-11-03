import { useCallback, useRef, useSyncExternalStore } from "react";
import useAuthStore from "@/core/stores/AuthStore";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { deleteCardModel } from "@/core/helpers/ModelHelper";
import { TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ProjectColumn, ProjectCard, GlobalRelationshipType, MetadataModel, ProjectChecklist } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import { validateBoardCardsResponse, readbackOmittedMoves } from "@/controllers/api/board/CrossBoardMoveReadback";
import { mergeLinkedResourceProjection } from "@/controllers/api/board/mergeLinkedResourceProjection";
import { useQueryClient, type QueryFunctionContext } from "@tanstack/react-query";

export interface IGetCardsForm {
    project_uid: string;
}

export interface IGetCardsResponse {
    isUpdated: true;
    snapshot: IBoardCardsSnapshot;
}

export interface IBoardCardsSnapshot {
    projectUID: string;
    actorUID: string;
    session: number;
    cardUIDs: string[];
    receivedAt: number;
    scope: "full_response";
}

/** Observe provenance only; ordinary board cards continue to include realtime additions. */
export function useBoardCardsSnapshot(projectUID: string): IBoardCardsSnapshot | undefined {
    const client = useQueryClient();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const subscribe = useCallback((notify: () => void) => client.getQueryCache().subscribe(notify), [client]);
    const getState = useCallback(
        () => client.getQueryState<IGetCardsResponse>([`get-cards-${projectUID}`, { project_uid: projectUID }, actorUID, session]),
        [client, projectUID, actorUID, session]
    );
    const state = useSyncExternalStore(subscribe, getState, getState);
    const snapshot = state?.data?.snapshot;
    if (
        state?.status !== "success" ||
        state.isInvalidated ||
        !snapshot ||
        snapshot.projectUID !== projectUID ||
        snapshot.actorUID !== actorUID ||
        snapshot.session !== session
    )
        return undefined;
    return snapshot;
}

interface IGetProjectCardMetadataResponse {
    metadata: Record<string, Record<string, string>>;
}

const useGetCards = (params: IGetCardsForm, options?: TQueryOptions<unknown, IGetCardsResponse>) => {
    const { query } = useQueryMutation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const currentBoard = useRef(params.project_uid);
    currentBoard.current = params.project_uid;
    const assertCurrent = (signal: AbortSignal) => {
        signal.throwIfAborted();
        const auth = useAuthStore.getState();
        if (!actorUID || auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session || currentBoard.current !== params.project_uid) {
            throw new Error("Board cards request no longer authorized");
        }
    };

    const getCards = async ({ signal }: QueryFunctionContext): Promise<IGetCardsResponse> => {
        const existingCardUIDs = new Set(ProjectCard.Model.getModels((model) => model.project_uid === params.project_uid).map((model) => model.uid));
        const existingColumnUIDs = new Set(
            ProjectColumn.Model.getModels((model) => model.project_uid === params.project_uid).map((model) => model.uid)
        );
        const url = Utils.String.format(Routing.API.BOARD.GET_CARDS, { uid: params.project_uid });
        const res = await api.get(url, {
            signal,
            params: { include_automation: false },
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });
        assertCurrent(signal);
        const data = res.data;
        validateBoardCardsResponse(data, params.project_uid);
        const responseCardUIDs = new Set<string>(data.cards.map((card: ProjectCard.Interface) => card.uid));
        await readbackOmittedMoves(params.project_uid, new Set([...existingCardUIDs].filter((uid) => !responseCardUIDs.has(uid))), signal, () =>
            assertCurrent(signal)
        );
        assertCurrent(signal);
        const cards = res.data.cards.map((card: ProjectCard.Interface) => {
            if (!card.linked_resource) {
                // An omitted projection is unknown; never retain a previous reader's cached state.
                return { ...card, work_state: card.work_state };
            }

            const existing = ProjectCard.Model.getModel(card.uid)?.linked_resource;
            return {
                ...card,
                work_state: card.work_state,
                linked_resource: mergeLinkedResourceProjection(card.linked_resource, existing),
            };
        });
        const cardUIDs = new Set<string>(cards.map((card: ProjectCard.Interface) => card.uid));
        const columnUIDs = new Set<string>(res.data.columns.map((column: ProjectColumn.TModel) => column.uid));

        ProjectCard.Model.fromArray(cards, true);
        GlobalRelationshipType.Model.fromArray(res.data.global_relationships, true);
        ProjectColumn.Model.fromArray(res.data.columns, true);
        ProjectChecklist.Model.fromArray(res.data.checklists, true);

        ProjectCard.Model.getModels(
            (model) => model.project_uid === params.project_uid && existingCardUIDs.has(model.uid) && !cardUIDs.has(model.uid)
        ).forEach((model) => {
            // Archive omissions do not revoke a flipped card; availability owns that decision.
            deleteCardModel(model.uid, true, false);
        });
        ProjectColumn.Model.deleteModels(
            (model) => model.project_uid === params.project_uid && existingColumnUIDs.has(model.uid) && !columnUIDs.has(model.uid)
        );

        return {
            isUpdated: true,
            snapshot: {
                projectUID: params.project_uid,
                actorUID: actorUID!,
                session,
                cardUIDs: [...responseCardUIDs],
                receivedAt: Date.now(),
                scope: "full_response",
            },
        };
    };

    const result = query([`get-cards-${params.project_uid}`, params, actorUID, session], getCards, {
        ...options,
        enabled: (request) => !!actorUID && (typeof options?.enabled === "function" ? options.enabled(request) : (options?.enabled ?? true)),
        retry: 0,
        refetchInterval: Infinity,
        refetchOnWindowFocus: false,
    });

    const currentSnapshot = useRef(result.dataUpdatedAt);
    currentSnapshot.current = result.dataUpdatedAt;
    const snapshotUpdatedAt = result.dataUpdatedAt;

    // Optional enrichment must never delay or reject the authorized board snapshot.
    query(
        ["get-board-card-metadata", params.project_uid, actorUID, session, result.dataUpdatedAt],
        async ({ signal }) => {
            const url = Utils.String.format(Routing.API.METADATA.PROJECT_CARDS, { uid: params.project_uid });
            const res = await api.get<IGetProjectCardMetadataResponse>(url, {
                signal,
                params: { board_visible_only: true },
                env: { interceptToast: false } as never,
            });
            assertCurrent(signal);
            if (currentSnapshot.current !== snapshotUpdatedAt) throw new Error("Board metadata snapshot was replaced");
            const models: MetadataModel.Interface[] = Object.entries(res.data.metadata ?? {})
                .filter(([uid]) => ProjectCard.Model.getModel(uid)?.project_uid === params.project_uid)
                .map(([uid, metadata]) => ({ uid, type: "card", metadata, created_at: new Date(), updated_at: new Date() }));
            MetadataModel.Model.fromArray(models, true);
            return res.data;
        },
        {
            // One attempt per successful snapshot; observers cannot retry a failed older snapshot.
            enabled: (enrichment) => result.isEnabled && result.isSuccess && !result.isFetching && enrichment.state.status !== "error",
            staleTime: Infinity,
            gcTime: 0,
            retry: 0,
            refetchInterval: Infinity,
            refetchOnWindowFocus: false,
        }
    );

    return result;
};

export default useGetCards;
