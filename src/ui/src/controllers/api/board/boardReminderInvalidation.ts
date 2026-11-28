import useAuthStore from "@/core/stores/AuthStore";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

let currentChannel: { actorUID: string; channel: BroadcastChannel } | undefined;

export function publishBoardReminderInvalidation(actorUID: string, projectUID: string) {
    if (currentChannel?.actorUID === actorUID) currentChannel.channel.postMessage(projectUID);
}

export function useBoardReminderInvalidation(queryClient: QueryClient) {
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    const session = useAuthStore((state) => state.getSessionVersion());

    useEffect(() => {
        if (!actorUID || typeof BroadcastChannel === "undefined") return;
        const channel = new BroadcastChannel(`langboard:board-reminder:${actorUID}`);
        currentChannel = { actorUID, channel };

        channel.onmessage = ({ data: projectUID }: MessageEvent<unknown>) => {
            if (typeof projectUID !== "string" || !projectUID) return;
            const auth = useAuthStore.getState();
            if (auth.currentUser?.uid !== actorUID || auth.getSessionVersion() !== session) return;
            // The message only triggers an authorized read. Reminder values come from the server.
            void queryClient.invalidateQueries({
                queryKey: [`get-cards-${projectUID}`, { project_uid: projectUID }, actorUID, session],
                exact: true,
            });
        };
        return () => {
            if (currentChannel?.channel === channel) currentChannel = undefined;
            channel.close();
        };
    }, [actorUID, queryClient, session]);
}
