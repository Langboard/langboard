import {
    QueryClient,
    QueryFunction,
    QueryKey,
    UseMutationOptions,
    UseMutationResult,
    UseQueryOptions,
    UseQueryResult,
    useMutation,
    useQuery,
    useQueryClient,
} from "@tanstack/react-query";

export type TQueryOptions<TQueryFnData = unknown, TData = TQueryFnData, TError = Error> = Omit<
    UseQueryOptions<TQueryFnData, TError, TData>,
    "queryFn" | "queryKey"
> & {
    interceptToast?: bool;
};

export type TQueryFunction<TQueryFnData = unknown, TData = TQueryFnData> = QueryFunction<TQueryFnData, QueryKey, TData>;
export type TMutationOptions<TVariables = unknown, TData = unknown, TContext = unknown, TError = Error> = UseMutationOptions<
    TData,
    TError,
    TVariables,
    TContext
> & { interceptToast?: bool };

export const useQueryMutation = (queryClient: QueryClient = useQueryClient()) => {
    function query<TQueryFnData = unknown, TData = TQueryFnData, TQueryKey extends QueryKey = QueryKey, TError = Error>(
        queryKey: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>["queryKey"],
        queryFn: UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>["queryFn"],
        options: Omit<UseQueryOptions<TQueryFnData, TError, TData, TQueryKey>, "queryFn" | "queryKey"> = {}
    ): UseQueryResult<TData, TError> {
        return useQuery(
            {
                queryKey,
                queryFn,
                ...options,
            },
            queryClient
        );
    }

    function mutate<TVariables = unknown, TData = unknown, TContext = unknown, TError = Error>(
        mutationKey: UseMutationOptions<TData, TError, TVariables, TContext>["mutationKey"],
        mutationFn: UseMutationOptions<TData, TError, TVariables, TContext>["mutationFn"],
        options: Omit<UseMutationOptions<TData, TError, TVariables, TContext>, "mutationFn" | "mutationKey"> = {}
    ): UseMutationResult<TData, TError, TVariables, TContext> {
        return useMutation<TData, TError, TVariables, TContext>(
            {
                mutationKey,
                mutationFn,
                onSettled: (data, error, variables, onMutateResult, context) => {
                    queryClient.invalidateQueries({ queryKey: mutationKey });
                    if (options.onSettled) {
                        options.onSettled(data, error, variables, onMutateResult, context);
                    }
                },
                ...options,
            },
            queryClient
        );
    }

    return { query, mutate, queryClient };
};
