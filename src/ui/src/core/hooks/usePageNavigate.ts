import { usePageNavigationScope } from "@/core/providers/PageNavigationScope";
import { Utils } from "@langboard/core/utils";
import { useCallback, useRef } from "react";
import { NavigateOptions, To, useNavigate } from "react-router";

export interface INavigationFunction {
    (to: To | number, options?: IPageNavigateOptions): void;
    (delta: number): void;
}

export interface IPageNavigateOptions extends Omit<NavigateOptions, "viewTransition" | "flushSync"> {
    smooth?: bool;
    fromCardLocation?: boolean;
}

export const usePageNavigate = () => {
    const baseNavigate = useNavigate();
    const scope = usePageNavigationScope();
    const navigate: INavigationFunction = useCallback(
        (to: To | number, options?: IPageNavigateOptions) => {
            if (Utils.Type.isNumber(to)) {
                return scope ? scope.navigate(to) : baseNavigate(to);
            }

            const { fromCardLocation, smooth, ...navigateOptions } = options ?? {};

            const routerOptions: NavigateOptions = { ...navigateOptions, viewTransition: smooth || false };
            return scope ? scope.navigate(to, routerOptions, fromCardLocation) : baseNavigate(to, routerOptions);
        },
        [baseNavigate, scope]
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
