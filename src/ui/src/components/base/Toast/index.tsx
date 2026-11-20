/* eslint-disable @typescript-eslint/no-explicit-any */
"use client";

import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";
import { ExternalToast, ToastT, Toaster, toast, useSonner } from "sonner";
import Button from "@/components/base/Button";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";

export type { ToastT as IToast };

type AreaProps = React.ComponentProps<typeof Toaster>;
type TExternalToast = Omit<ExternalToast, "action" | "actionButtonStyle" | "cancel" | "cancelButtonStyle"> & {
    actions?: React.JSX.Element[];
    useCloseButton?: bool | true;
};
type TPromise<Data = any> = Promise<Data> | (() => Promise<Data>);
type TPromiseResult<Data = any> = string | React.ReactNode | ((data: Data) => React.ReactNode | string | Promise<React.ReactNode | string>);
type TPromiseExternalToast = Omit<TExternalToast, "description">;
type TPromiseData<ToastData = any> = TPromiseExternalToast & {
    loading?: string;
    success?: TPromiseResult<ToastData>;
    error?: TPromiseResult;
    description?: TPromiseResult;
    finally?: () => void | Promise<void>;
};
type TConvertedPromiseData<ToastData = any> = TPromiseExternalToast & {
    loading?: React.ReactNode;
    success?: TPromiseResult<ToastData>;
    error?: TPromiseResult;
    description?: TPromiseResult;
    finally?: () => void | Promise<void>;
};

const Area = ({ ...props }: AreaProps) => {
    const { theme } = useTheme();
    const [t] = useTranslation();

    const sharedToastClassNames = "group toast group-[.toaster]:shadow-lg";

    return (
        <Toaster
            theme={theme as AreaProps["theme"]}
            containerAriaLabel={t("notification.Notifications")}
            className="toaster group pointer-events-auto"
            toastOptions={{
                closeButtonAriaLabel: t("common.Close"),
                classNames: {
                    toast: cn(sharedToastClassNames, "group-[.toaster]:text-foreground group-[.toaster]:border-border leading-none"),
                    description: "group-[.toast]:text-muted-foreground",
                    actionButton: "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
                    cancelButton: "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
                    error: cn(sharedToastClassNames, "group-[.toaster]:bg-red group-[.toaster]:text-red-600"),
                    success: cn(sharedToastClassNames, "group-[.toaster]:bg-green group-[.toaster]:text-green-600"),
                    warning: cn(sharedToastClassNames, "group-[.toaster]:bg-yellow group-[.toaster]:text-yellow-600"),
                    info: cn(sharedToastClassNames, "group-[.toaster]:bg-blue group-[.toaster]:text-blue-600"),
                    icon: "mt-0.5",
                    content: "max-w-[calc(100%_-_theme(spacing.12))] group-data-[type=loading]:max-w-full",
                },
            }}
            icons={{
                loading: <IconComponent icon="loader-circle" size="4" strokeWidth="3" className="mt-0.5 animate-spin" />,
            }}
            {...props}
        />
    );
};

const ActionList = ({ isLast = false, children }: { isLast?: bool; children: React.ReactNode }) => (
    <div className={cn("ml-auto flex items-center gap-1", isLast ? "" : "mr-7")}>{children}</div>
);
const ToastCloseButton = ({ id }: { id: string | number }) => {
    const [t] = useTranslation();
    return (
        <Button
            type="button"
            aria-label={t("common.Close")}
            variant="ghost"
            className={cn("absolute right-3 ml-auto size-6 p-1 group-data-[type=loading]:hidden")}
            size="icon"
            onClick={() => toast.dismiss(id)}
        >
            <IconComponent icon="x" size="4" />
        </Button>
    );
};

function createToastData<ToastData>(data?: TExternalToast | TPromiseData<ToastData>): ExternalToast {
    const { useCloseButton = true, id = Utils.String.Token.uuid() } = data || {};

    if (!data) {
        if (useCloseButton) {
            return { cancel: <ToastCloseButton id={id} />, id };
        }

        return { id };
    }

    const toastData = { ...data, id } as (TExternalToast | TConvertedPromiseData<ToastData>) & ExternalToast;

    if ((data as TPromiseData<ToastData>).loading) {
        (toastData as TConvertedPromiseData<ToastData>).loading = (
            <div className="flex items-center gap-2">{(data as TPromiseData<ToastData>).loading}</div>
        );
    }

    if (useCloseButton) {
        delete toastData.useCloseButton;
        toastData.cancel = <ToastCloseButton id={id} />;
    }

    if (toastData.actions) {
        const actions = toastData.actions;
        delete toastData.actions;
        toastData.action = <ActionList isLast={!useCloseButton}>{...actions}</ActionList>;
    }

    return toastData;
}

function Add(message: string | React.ReactNode, data?: TExternalToast): string | number {
    return toast(message, createToastData(data));
}

Add.success = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.success(message, createToastData(data));
Add.info = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.info(message, createToastData(data));
Add.warning = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.warning(message, createToastData(data));
Add.error = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.error(message, createToastData(data));
Add.custom = (jsx: (id: number | string) => React.ReactElement, data?: TExternalToast): string | number => toast.custom(jsx, createToastData(data));
Add.message = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.message(message, createToastData(data));
Add.promise = function <ToastData>(
    promise: TPromise<ToastData>,
    data?: TPromiseData<ToastData>
): (string | number) & { unwrap: () => Promise<ToastData> } {
    return toast.promise(promise, createToastData(data)) as (string | number) & { unwrap: () => Promise<ToastData> };
};
Add.dismiss = toast.dismiss;
Add.loading = (message: string | React.ReactNode, data?: TExternalToast): string | number => toast.loading(message, createToastData(data));

export default {
    ActionList,
    Add,
    Area,
    useToast: useSonner,
};
