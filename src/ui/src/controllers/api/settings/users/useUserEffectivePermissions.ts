import { api } from "@/core/helpers/Api";
import useAuthStore from "@/core/stores/AuthStore";
import { isAxiosError } from "axios";
import { useEffect, useState } from "react";

export interface UserEffectivePermissions {
    user_uid: string;
    resource: { type: "project" | "card"; uid: string };
    action: "read" | "card.read" | "card.update";
    allowed: boolean;
    sources: {
        type:
            | "project_owner"
            | "administrator_project_membership"
            | "administrator_action_override"
            | "full_admin_access_policy"
            | "direct_project_role"
            | "native_card_visibility";
        uid?: string;
    }[];
    reason: string | null;
    channel: string;
}

export default function useUserEffectivePermissions(uid: string, projectUID: string, cardUID = "", action: "read" | "card_update" = "read") {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const [revision, setRevision] = useState(0);
    const identity = JSON.stringify([actorUID, uid, projectUID, cardUID, action, revision]);
    const [result, setResult] = useState<{ identity: string; data?: UserEffectivePermissions; error?: number }>();
    useEffect(() => {
        if (!projectUID || !actorUID) return;
        const abort = new AbortController();
        const identityVersion = useAuthStore.getState().getIdentityVersion();
        const current = () => !abort.signal.aborted && identityVersion === useAuthStore.getState().getIdentityVersion();
        api.get<{ inspection: UserEffectivePermissions }>(`/settings/users/${uid}/effective-permissions`, {
            signal: abort.signal,
            params: { project_uid: projectUID, ...(cardUID ? { card_uid: cardUID } : {}), ...(action === "card_update" ? { action } : {}) },
            env: { interceptToast: true } as never,
        })
            .then(({ data }) => {
                if (current()) setResult({ identity, data: data.inspection });
            })
            .catch((error: unknown) => {
                if (current()) setResult({ identity, error: isAxiosError(error) ? error.response?.status || 500 : 500 });
            });
        return () => abort.abort();
    }, [actorUID, uid, projectUID, cardUID, action, identity]);
    const current = projectUID && result?.identity === identity ? result : undefined;
    return {
        data: current?.data,
        error: current?.error,
        loading: !!projectUID && !current,
        retry: () => setRevision((value) => value + 1),
    };
}
