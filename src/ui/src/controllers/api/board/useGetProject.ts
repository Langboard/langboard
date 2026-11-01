import { useRef } from "react";
import useAuthStore from "@/core/stores/AuthStore";
import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { Project } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import refreshProjectColumnDock from "@/controllers/api/board/refreshProjectColumnDock";
import type { QueryFunctionContext } from "@tanstack/react-query";

export interface IGetProjectForm {
    uid: string;
}

export interface IGetProjectResponse {
    project: Project.TModel;
}

const useGetProject = (form: IGetProjectForm, options?: TQueryOptions<unknown, IGetProjectResponse>) => {
    const { query } = useQueryMutation();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());
    const currentBoard = useRef(form.uid);
    currentBoard.current = form.uid;

    const getProject = async ({ signal }: QueryFunctionContext) => {
        const url = Utils.String.format(Routing.API.BOARD.GET, { uid: form.uid });
        const res = await api.get(url, {
            signal,
            params: { include_automation: false },
            timeout: 15_000,
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        signal.throwIfAborted();
        const auth = useAuthStore.getState();
        if (!actorUID || auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session || currentBoard.current !== form.uid) {
            throw new Error("Board request no longer authorized");
        }
        if (
            !res.data?.project ||
            res.data.project.uid !== form.uid ||
            res.data.project.labels?.some((label: { project_uid: string }) => label.project_uid !== form.uid)
        ) {
            throw new Error("Board response belongs to another board");
        }
        const project = Project.Model.fromOne(res.data.project);
        project.last_viewed_at = new Date();

        // Record only an accepted board snapshot. Visit failure must not fail or delay the board.
        void api
            .post(`${url}/visit`, undefined, {
                signal,
                timeout: 15_000,
                env: { interceptToast: true } as never,
            })
            .catch((error) => setupApiErrorHandler({}).handle(error));

        // Dock enrichment must not delay the authorized board or turn its failure into a board failure.
        void refreshProjectColumnDock(form.uid).catch((error) => setupApiErrorHandler({}).handle(error));

        return {
            project,
        };
    };

    const result = query([`get-project-${form.uid}`, actorUID, session], getProject, {
        ...options,
        enabled: (request) => !!actorUID && (typeof options?.enabled === "function" ? options.enabled(request) : (options?.enabled ?? true)),
        placeholderData: (previous, previousQuery) => {
            if (!previousQuery || previousQuery.queryKey[1] !== actorUID || previousQuery.queryKey[2] !== session) return undefined;
            return typeof options?.placeholderData === "function" ? options.placeholderData(previous, previousQuery) : options?.placeholderData;
        },
        retry: 0,
        refetchInterval: Infinity,
        refetchOnWindowFocus: false,
    });
    return result;
};

export default useGetProject;
