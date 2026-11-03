import { useCallback } from "react";
import { isAxiosError } from "axios";
import { api } from "@/core/helpers/Api";
import type { ICollaborativeRestoreRequest } from "@/components/Editor/collaborativeInitialization";

const useRestoreCollaborativeDraft = () =>
    useCallback(async (request: ICollaborativeRestoreRequest, signal?: AbortSignal): Promise<boolean | "busy"> => {
        try {
            await api.post("/editor-sync/rich/patch-request", request, { signal, env: { interceptToast: true } as never });
            return true;
        } catch (error) {
            if (isAxiosError(error) && error.response?.status === 409) {
                if (error.response.data?.reason === "busy") return "busy";
                if (error.response.data?.reason === "conflict") return false;
            }
            throw error;
        }
    }, []);

export default useRestoreCollaborativeDraft;
