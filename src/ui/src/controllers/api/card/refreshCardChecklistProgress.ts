import { isCancelledError, type Query, type QueryClient } from "@tanstack/react-query";
import { ProjectCard } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import { readCardDetails, type IGetCardDetailsResponse } from "./useGetCardDetails";

const scheduled = new WeakMap<QueryClient, Map<string, Promise<void>>>();

/** Same-turn subscribers share one read; later events cancel an older read. */
export function refreshCardChecklistProgress(projectUID: string, cardUID: string, client: QueryClient): Promise<void> {
    const auth = useAuthStore.getState();
    const actor = auth.currentUser?.uid;
    if (!actor) return Promise.resolve();
    const session = auth.getSessionVersion();
    const identity = auth.getIdentityVersion();
    const key = `${projectUID}:${cardUID}:${actor}:${session}:${identity}`;
    let pending = scheduled.get(client);
    if (!pending) scheduled.set(client, (pending = new Map()));
    const existing = pending.get(key);
    if (existing) return existing;
    const promise = new Promise<void>((resolve, reject) => {
        window.setTimeout(() => {
            pending.delete(key);
            const latest = useAuthStore.getState();
            if (latest.currentUser?.uid !== actor || latest.getSessionVersion() !== session || latest.getIdentityVersion() !== identity) {
                resolve();
                return;
            }
            refreshProgress(projectUID, cardUID, client).then(resolve, reject);
        }, 0);
    });
    pending.set(key, promise);
    return promise;
}

async function refreshProgress(projectUID: string, cardUID: string, client: QueryClient) {
    const auth = useAuthStore.getState();
    const actorUID = auth.currentUser?.uid;
    if (!actorUID) return;
    const sessionVersion = auth.getSessionVersion();
    const identityVersion = auth.getIdentityVersion();
    const card = ProjectCard.Model.getModel(cardUID);
    const currentSession = () => {
        const latest = useAuthStore.getState();
        return (
            latest.currentUser?.uid === actorUID && latest.getSessionVersion() === sessionVersion && latest.getIdentityVersion() === identityVersion
        );
    };
    const detail = ({ queryKey }: Query) =>
        queryKey[0] === `get-card-details-${projectUID}-${cardUID}` &&
        queryKey[1] === actorUID &&
        queryKey[2] === sessionVersion &&
        queryKey[4] === identityVersion;
    const board = ({ queryKey }: Query) => queryKey[0] === `get-cards-${projectUID}` && queryKey[2] === actorUID && queryKey[3] === sessionVersion;
    if (card?.project_uid !== projectUID) {
        // An unknown/new card still needs the existing authorized board recovery.
        void client.invalidateQueries({ predicate: board });
        return;
    }
    const isCurrent = () => currentSession() && ProjectCard.Model.getModel(cardUID) === card && card.project_uid === projectUID;
    try {
        const progressKey = ["card-checklist-progress", projectUID, cardUID, actorUID, sessionVersion, identityVersion];
        await client.cancelQueries({ queryKey: progressKey, exact: true });
        if (!isCurrent()) return;
        await client.fetchQuery({
            queryKey: progressKey,
            retry: 0,
            gcTime: 0,
            queryFn: async ({ signal }) => {
                const withChecklists = client
                    .getQueryCache()
                    .findAll({ predicate: detail })
                    .some(
                        (query) => query.isActive() && (query.queryKey[3] === "legacy" || String(query.queryKey[3]).split(",").includes("checklists"))
                    );
                // An older body request must not overwrite newer authority/progress.
                await client.cancelQueries({ predicate: detail });
                signal.throwIfAborted();
                if (!isCurrent()) throw new Error("Card progress request no longer authorized");
                const fresh = await readCardDetails(
                    {
                        project_uid: projectUID,
                        card_uid: cardUID,
                        sections: withChecklists ? ["checklists", "project_columns"] : ["project_columns"],
                    },
                    {
                        signal,
                        queryClient: client,
                        actorUID,
                        sessionVersion,
                        identityVersion,
                        interceptToast: true,
                        isCurrent,
                    }
                );
                if (!isCurrent()) throw new Error("Card progress response no longer authorized");
                client.setQueriesData<IGetCardDetailsResponse>({ predicate: detail }, (previous) =>
                    previous
                        ? {
                              ...previous,
                              card: fresh.card,
                              checklists:
                                  fresh.checklists !== undefined &&
                                  (previous.loaded_sections?.includes("checklists") || previous.loaded_sections === undefined)
                                      ? fresh.checklists
                                      : previous.checklists,
                              action_authority: fresh.action_authority,
                              workflow_completion: fresh.workflow_completion,
                              whispers_allowed: fresh.whispers_allowed,
                          }
                        : previous
                );
                if (!withChecklists) {
                    await client.invalidateQueries({ predicate: (query) => detail(query) && !query.isActive(), refetchType: "none" });
                }
                // Only a detail initially opened during this event still needs its body.
                await client.invalidateQueries({ predicate: (query) => detail(query) && query.state.data === undefined });
                return fresh;
            },
        });
    } catch (error) {
        if (isCancelledError(error) || !currentSession()) return;
        // Revocation, deletion and transport errors retain the existing recovery path.
        void client.invalidateQueries({ predicate: board });
        void client.invalidateQueries({ predicate: detail });
    }
}
