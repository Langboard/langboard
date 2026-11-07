import { useEffect, useId } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import { SocketEvents } from "@langboard/core/constants";
import { ESocketTopic } from "@langboard/core/enums";
import useAuthStore from "@/core/stores/AuthStore";
import { acquireProjectWorkload } from "./projectWorkloadSubscriptions";

/** Share Dashboard topics between project discovery and loaded card lists. */
export default function useProjectWorkload(projectUIDs: string) {
    const queryClient = useQueryClient();
    const subscriber = useId();
    const actorUID = useAuthStore((state) => state.currentUser?.uid);
    useEffect(() => {
        const socket = useSocketOutsideProvider();
        return acquireProjectWorkload(queryClient, projectUIDs.split(",").filter(Boolean), {
            subscribe: (uids, onSubscribed) => socket.subscribe(ESocketTopic.Dashboard, uids, onSubscribed),
            unsubscribe: (uids) => socket.unsubscribe(ESocketTopic.Dashboard, uids),
            listen: (uid, callback) => {
                const props = {
                    topic: ESocketTopic.Dashboard as const,
                    topicId: uid,
                    event: SocketEvents.SERVER.DASHBOARD.PROJECT.ACTIVITY_RECORDED.replace("{uid}", uid),
                    eventKey: `project-workload-${subscriber}-${uid}`,
                    callback,
                };
                socket.on(props);
                return () => socket.off(props);
            },
            listenOpen: (callback) => {
                const props = { event: "open" as const, eventKey: `project-workload-open-${subscriber}`, callback };
                socket.on(props);
                return () => socket.off(props);
            },
        });
    }, [actorUID, projectUIDs, queryClient, subscriber]);
}
