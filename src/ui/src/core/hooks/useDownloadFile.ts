import { API_URL } from "@/constants";
import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { useCallback, useEffect, useRef, useState } from "react";

export interface IUseDownloadFileProps {
    url: string;
    filename?: string;
    onError?: () => void;
    onFinally?: () => void;
}

const useDownloadFile = ({ url, filename, onError, onFinally }: IUseDownloadFileProps, deps?: unknown[]) => {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const sessionVersion = useAuthStore((state) => state.getSessionVersion());
    const request = useRef<AbortController | undefined>(undefined);
    const identity = JSON.stringify([url, filename, actorUID, sessionVersion]);
    const latestIdentity = useRef(identity);
    latestIdentity.current = identity;
    const [isDownloading, setIsDownloading] = useState(false);
    const isDownloadingRef = useRef(isDownloading);
    useEffect(() => {
        request.current?.abort();
        request.current = undefined;
        isDownloadingRef.current = false;
        setIsDownloading(false);
        return () => request.current?.abort();
    }, [identity]);
    const download = useCallback(() => {
        if (isDownloadingRef.current) {
            return;
        }

        setIsDownloading(true);
        isDownloadingRef.current = true;

        const abort = new AbortController();
        request.current = abort;
        const current = () =>
            !abort.signal.aborted &&
            identity === latestIdentity.current &&
            actorUID === useAuthStore.getState().currentUser?.uid &&
            sessionVersion === useAuthStore.getState().getSessionVersion();
        api.get(url.replace(API_URL, ""), { responseType: "blob", signal: abort.signal })
            .then((response) => {
                if (!current()) return;
                const blob = response.data;
                const url = URL.createObjectURL(blob);

                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = filename ?? url.split("/").pop()!;
                anchor.click();
                anchor.remove();
                window.URL.revokeObjectURL(url);
            })
            .catch(() => {
                if (current()) onError?.();
            })
            .finally(() => {
                if (!current()) return;
                request.current = undefined;
                onFinally?.();
                setIsDownloading(false);
                isDownloadingRef.current = false;
            });
    }, [url, filename, onError, onFinally, actorUID, sessionVersion, identity, ...(deps ?? [])]);

    return { download, isDownloading };
};

export default useDownloadFile;
