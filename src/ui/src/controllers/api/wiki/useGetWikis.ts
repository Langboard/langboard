import { Routing } from "@langboard/core/constants";
import { api } from "@/core/helpers/Api";
import { TQueryOptions, useQueryMutation } from "@/core/helpers/QueryMutation";
import { ProjectWiki, User } from "@/core/models";
import { Utils } from "@langboard/core/utils";
import useAuthStore from "@/core/stores/AuthStore";
import { workspaceSummaryAuthScope } from "@/controllers/api/WorkspaceSummaryInvalidation";
import type { QueryFunctionContext } from "@tanstack/react-query";

export interface IGetWikisForm {
    project_uid: string;
}

export interface IGetWikisResponse {
    wikis: ProjectWiki.TModel[];
    project_members: User.TModel[];
}

const useGetWikis = (params: IGetWikisForm, options?: TQueryOptions<unknown, IGetWikisResponse>) => {
    const { query } = useQueryMutation();

    const getWikis = async ({ signal }: QueryFunctionContext) => {
        const authScope = workspaceSummaryAuthScope(useAuthStore.getState());
        const url = Utils.String.format(Routing.API.BOARD.WIKI.GET_ALL, { uid: params.project_uid });
        const res = await api.get(url, {
            signal,
            timeout: 15_000,
            env: {
                interceptToast: options?.interceptToast,
            } as never,
        });

        if (signal.aborted || workspaceSummaryAuthScope(useAuthStore.getState()) !== authScope) {
            throw new Error("Wiki read superseded");
        }
        const wikis = res.data.wikis.map((row: ProjectWiki.Interface) => {
            const current = ProjectWiki.Model.getModel(row.uid);
            if (current && (row.editor_generation ?? 0) < current.editor_generation) {
                // An older response must not roll back a secretization event.
                // Preserve other metadata without restoring the invalidated editor state.
                return {
                    ...row,
                    content: row.forbidden ? undefined : current.content,
                    editor_generation: current.editor_generation,
                    forbidden: row.forbidden || current.forbidden,
                };
            }
            return row;
        });
        return {
            wikis: ProjectWiki.Model.fromArray(wikis, true),
            project_members: User.Model.fromArray(res.data.project_members),
        };
    };

    const result = query([`get-wikis-${params.project_uid}`], getWikis, {
        ...options,
        retry: 0,
        refetchInterval: Infinity,
        refetchOnWindowFocus: false,
    });

    return result;
};

export default useGetWikis;
