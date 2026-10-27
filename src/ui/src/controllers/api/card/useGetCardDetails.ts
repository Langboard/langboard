import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import {
    ProjectCardBotScope,
    GlobalRelationshipType,
    ProjectCard,
    ProjectCardAttachment,
    ProjectCheckitem,
    ProjectChecklist,
    ProjectColumn,
    ProjectLabel,
} from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { Utils } from "@langboard/core/utils";

export type CardDetailSection =
    | "description"
    | "checklists"
    | "attachments"
    | "linked_wikis"
    | "global_relationships"
    | "project_columns"
    | "project_labels"
    | "bot_scopes"
    | "execution_receipts"
    | "agent_execution";

export interface IGetCardDetailsForm {
    project_uid: string;
    card_uid: string;
    sections?: CardDetailSection[];
}

export interface IGetCardDetailsResponse {
    card: ProjectCard.TModel;
    action_authority?: Readonly<{
        role_actions: readonly string[];
        native_card: boolean;
        is_check_card: boolean;
        completed: boolean | undefined;
    }>;
    workflow_completion?: Readonly<{
        source_column_uid: string;
        completed: boolean | undefined;
        destinations: readonly Readonly<{ uid: string; name: string }>[];
    }>;
    loaded_sections?: CardDetailSection[];
    whispers_allowed?: boolean;
    attachments?: ProjectCardAttachment.TModel[];
    linked_wikis?: { wiki_uid: string; title: string }[];
    checklists?: ProjectChecklist.TModel[];
    global_relationships?: GlobalRelationshipType.TModel[];
    project_columns?: ProjectColumn.TModel[];
    project_labels?: ProjectLabel.TModel[];
    bot_scopes?: ProjectCardBotScope.TModel[];
    agent_execution?: {
        schema_version: 1;
        start_uid: string;
        request_uid: string;
        app_key: string;
        generation: number;
        reported_at: string;
        evaluated_at: string;
        authorization_expires_at: string;
        evidence_kind: "app_attestation";
        current_authorization: "authorized";
        process_liveness: "unknown";
    } | null;
    execution_receipts?: {
        generation: number;
        created_at: string;
        checklist_projection: { item_uid: string; kind: string; refs: string[]; is_checked: boolean }[];
        receipt: {
            status: string;
            summary: string;
            artifacts: { type: string; url: string }[];
            checklist_evidence: { item_uid: string; kind: string; refs: string[] }[];
        };
    }[];
}

const useGetCardDetails = (params: IGetCardDetailsForm, options?: TQueryOptions<IGetCardDetailsResponse, IGetCardDetailsResponse>) => {
    const { query, queryClient } = useQueryMutation();

    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore.getState().getSessionVersion();
    const identityVersion = useAuthStore((state) => state.getIdentityVersion());
    const selection = params.sections ? [...new Set(params.sections)].sort().join(",") : undefined;
    const getCardDetails = async ({ signal }: { signal: AbortSignal }) => {
        const url = Utils.String.format(Routing.API.BOARD.CARD.GET_DETAILS, { uid: params.project_uid, card_uid: params.card_uid });
        const res = await api.get(url, {
            signal,
            params: selection === undefined ? undefined : { sections: selection },
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        if (
            signal.aborted ||
            actorUID !== useAuthStore.getState().currentUser?.uid ||
            sessionVersion !== useAuthStore.getState().getSessionVersion() ||
            identityVersion !== useAuthStore.getState().getIdentityVersion()
        ) {
            throw new Error("Card detail request no longer belongs to the current session");
        }

        if (!res.data?.card || res.data.card.uid !== params.card_uid || res.data.card.project_uid !== params.project_uid) {
            // A moved card's minimal location response must never enter the detail model stores.
            void queryClient.invalidateQueries(
                {
                    predicate: ({ queryKey, state }) =>
                        queryKey[0] === "resolve-card-location" &&
                        queryKey[1] === params.project_uid &&
                        queryKey[2] === params.card_uid &&
                        queryKey[3] === actorUID &&
                        queryKey[4] === sessionVersion &&
                        (state.data as { canonical_project_uid?: string } | undefined)?.canonical_project_uid === params.project_uid,
                },
                { cancelRefetch: false }
            );
            throw new Error("Card detail response belongs to another card or board");
        }

        // Capture workflow facts before model hydration: omitted fields must never inherit an old grant.
        const validUID = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9_-]+$/.test(value);
        const rawColumns: Record<string, unknown>[] = Array.isArray(res.data.project_columns) ? res.data.project_columns : [];
        const uidCounts = new Map<unknown, number>();
        rawColumns.forEach((column) => uidCounts.set(column?.uid, (uidCounts.get(column?.uid) ?? 0) + 1));
        const columns = rawColumns.filter(
            (column) =>
                column &&
                typeof column === "object" &&
                validUID(column.uid) &&
                column.project_uid === params.project_uid &&
                uidCounts.get(column.uid) === 1
        );
        const source = columns.find((column) => column.uid === res.data.card.project_column_uid);
        const workflowCompletion =
            res.data.card.is_check_card === false && source?.is_archive === false
                ? Object.freeze({
                      source_column_uid: source.uid as string,
                      completed: typeof res.data.card.work_state?.completed === "boolean" ? res.data.card.work_state.completed : undefined,
                      destinations: Object.freeze(
                          columns
                              .filter(
                                  (column) =>
                                      column.uid !== source.uid &&
                                      column.is_archive === false &&
                                      column.workflow_counts_as_completed === true &&
                                      typeof column.name === "string" &&
                                      column.name.trim().length > 0
                              )
                              .map((column) => Object.freeze({ uid: column.uid as string, name: column.name as string }))
                      ),
                  })
                : undefined;

        const checkitems =
            res.data.checklists === undefined
                ? []
                : ProjectCheckitem.Model.fromArray(
                      res.data.checklists.flatMap((checklist: { checkitems?: ProjectCheckitem.Interface[] }) => checklist.checkitems ?? []),
                      true
                  );
        const itemsByUID = new Map(checkitems.map((item) => [item.uid, item]));
        const checklists =
            res.data.checklists === undefined
                ? undefined
                : ProjectChecklist.Model.fromArray(
                      res.data.checklists.map((checklist: ProjectChecklist.IStore) => ({
                          ...checklist,
                          checkitems: (checklist.checkitems ?? []).map((item) => itemsByUID.get(item.uid)!),
                      })),
                      true
                  );

        return {
            workflow_completion: workflowCompletion,
            loaded_sections: selection?.split(",") as CardDetailSection[] | undefined,
            // Capabilities belong to this response. Model hydration merges omitted fields.
            action_authority: Object.freeze({
                role_actions: Object.freeze(
                    Array.isArray(res.data.card.current_auth_role_actions) &&
                        res.data.card.current_auth_role_actions.every((value: unknown) => typeof value === "string")
                        ? ([...res.data.card.current_auth_role_actions] as string[])
                        : []
                ),
                native_card: res.data.card.can_cross_board === true && !res.data.card.source_type && !res.data.card.linked_resource,
                is_check_card: res.data.card.is_check_card === true,
                completed: typeof res.data.card.completed === "boolean" ? res.data.card.completed : undefined,
            }),
            card: ProjectCard.Model.fromOne(res.data.card),
            whispers_allowed: res.data.whispers_allowed === true,
            attachments: res.data.attachments === undefined ? undefined : ProjectCardAttachment.Model.fromArray(res.data.attachments, true),
            linked_wikis: res.data.linked_wikis,
            checklists,
            global_relationships:
                res.data.global_relationships === undefined ? undefined : GlobalRelationshipType.Model.fromArray(res.data.global_relationships, true),
            project_columns: res.data.project_columns === undefined ? undefined : ProjectColumn.Model.fromArray(res.data.project_columns, true),
            project_labels: res.data.project_labels === undefined ? undefined : ProjectLabel.Model.fromArray(res.data.project_labels, true),
            bot_scopes: res.data.bot_scopes === undefined ? undefined : ProjectCardBotScope.Model.fromArray(res.data.bot_scopes, true),
            execution_receipts: res.data.execution_receipts,
            agent_execution: res.data.agent_execution,
        };
    };

    const result = query(
        [`get-card-details-${params.project_uid}-${params.card_uid}`, actorUID, sessionVersion, selection ?? "legacy", identityVersion],
        getCardDetails,
        {
            ...options,
            retry: 0,
            refetchInterval: Infinity,
            refetchOnWindowFocus: false,
        }
    );

    // Structural sharing may copy frozen plain objects; seal the delivered projection.
    if (result.data?.action_authority) {
        Object.freeze(result.data.action_authority.role_actions);
        Object.freeze(result.data.action_authority);
    }
    if (result.data?.workflow_completion) {
        result.data.workflow_completion.destinations.forEach(Object.freeze);
        Object.freeze(result.data.workflow_completion.destinations);
        Object.freeze(result.data.workflow_completion);
    }
    return result;
};

export default useGetCardDetails;
