import { useCallback, useMemo } from "react";
import { createSearchParams, type SetURLSearchParams } from "react-router";
import { usePageLocation } from "./usePageLocation";
import { usePageNavigateRef } from "./usePageNavigate";

export function usePageSearchParams(): [URLSearchParams, SetURLSearchParams] {
    const location = usePageLocation();
    const navigate = usePageNavigateRef();
    const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
    const setParams = useCallback<SetURLSearchParams>(
        (next, options) => {
            const value = typeof next === "function" ? next(new URLSearchParams(location.search)) : next;
            navigate({ pathname: location.pathname, search: createSearchParams(value).toString(), hash: location.hash }, options);
        },
        [location, navigate]
    );
    return [params, setParams];
}
