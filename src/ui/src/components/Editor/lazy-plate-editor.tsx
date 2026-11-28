import { lazy, Suspense, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import Skeleton from "@/components/base/Skeleton";

const Editor = lazy(() => import("./plate-editor").then((module) => ({ default: module.PlateEditor })));

type Props = ComponentProps<typeof import("./plate-editor").PlateEditor>;

export function PlateEditor({ loadingFallback, ...props }: Props & { loadingFallback?: React.ReactNode }): React.JSX.Element {
    const [t] = useTranslation();
    return (
        <Suspense
            fallback={
                loadingFallback ?? (
                    <div role="status" aria-live="polite">
                        <span className="sr-only">{t("common.Loading...")}</span>
                        <Skeleton className="h-20 w-full" />
                    </div>
                )
            }
        >
            <Editor {...props} />
        </Suspense>
    );
}
