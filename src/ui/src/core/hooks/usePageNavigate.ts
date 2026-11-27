import { Utils } from "@langboard/core/utils";
import { useCallback, useRef } from "react";
import { NavigateOptions, To, useNavigate } from "react-router";

export interface INavigationFunction {
    (to: To | number, options?: IPageNavigateOptions): void;
    (delta: number): void;
}

export interface IPageNavigateOptions extends Omit<NavigateOptions, "viewTransition" | "flushSync"> {
    smooth?: bool;
}

export const usePageNavigate = () => {
    const baseNavigate = useNavigate();
    const navigate: INavigationFunction = useCallback(
        (to: To | number, options?: IPageNavigateOptions) => {
            if (Utils.Type.isNumber(to)) {
                return baseNavigate(to);
            }

            const navigateOptions = { ...options } as NavigateOptions;
            if (!options) {
                options = {};
            }

            if (Utils.Type.isNullOrUndefined(options.smooth)) {
                navigateOptions.viewTransition = false;
            } else if (options.smooth) {
                navigateOptions.viewTransition = true;
            }

            return baseNavigate(to, navigateOptions);
        },
        [baseNavigate]
    );

    return navigate;
};

export const usePageNavigateRef = () => {
    const navigate = usePageNavigate();
    const navigations = useRef(navigate);
    navigations.current = navigate;

    const stableNavigate: INavigationFunction = useCallback((to: To | number, options?: IPageNavigateOptions) => {
        if (Utils.Type.isNumber(to)) {
            return navigations.current(to);
        }

        return navigations.current(to, options);
    }, []) as INavigationFunction;

    return stableNavigate;
};
