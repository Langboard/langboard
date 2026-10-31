import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ESocketTopic } from "@langboard/core/enums";
import { useSocketOutsideProvider } from "@/core/providers/SocketProvider";
import useAuthStore from "@/core/stores/AuthStore";
import {
    acquireWorkspaceSummaryTopics,
    resetWorkspaceSummaryQueries,
    workspaceSummaryAuthScope,
} from "@/controllers/api/WorkspaceSummaryInvalidation";

interface WikiEvent {
    event: string;
    data: object;
}

export default function useWikiSummaryEvents(projectUID: string, authScope: string, privateWikiUIDs: string[]) {
    const client = useQueryClient();
    const privateKey = JSON.stringify([...new Set(privateWikiUIDs)].sort());
    useEffect(() => {
        if (!projectUID || !useAuthStore.getState().currentUser) return;
        const socket = useSocketOutsideProvider();
        let active = true;
        const refresh = (data: object) => {
            if (active && workspaceSummaryAuthScope(useAuthStore.getState()) === authScope) {
                resetWorkspaceSummaryQueries(client, "wiki-summaries", projectUID, data);
            }
        };
        const event = {
            topic: ESocketTopic.BoardWiki as const,
            topicId: projectUID,
            event: "*",
            eventKey: `wiki-summary-events-${authScope}-${projectUID}`,
            callback: ({ event: name, data }: WikiEvent) => {
                if (/^board:wiki:(created|deleted|details:changed|public:changed|assignees:updated|order:changed):/.test(name)) refresh(data);
            },
        };
        const open = { event: "open" as const, eventKey: event.eventKey, callback: (event: Event) => refresh(event) };
        socket.on(event);
        socket.on(open);
        const release = acquireWorkspaceSummaryTopics(socket, [[ESocketTopic.BoardWiki, projectUID]]);
        return () => {
            active = false;
            socket.off(event);
            socket.off(open);
            release();
        };
    }, [client, projectUID, authScope]);
    useEffect(() => {
        if (!useAuthStore.getState().currentUser) return;
        const socket = useSocketOutsideProvider();
        let active = true;
        const uids: string[] = JSON.parse(privateKey);
        const events = uids.map((uid) => ({
            topic: ESocketTopic.BoardWikiPrivate as const,
            topicId: uid,
            event: "*",
            eventKey: `wiki-summary-private-${authScope}-${projectUID}-${uid}`,
            callback: ({ event, data }: WikiEvent) => {
                if (active && workspaceSummaryAuthScope(useAuthStore.getState()) === authScope && event === `board:wiki:details:changed:${uid}`) {
                    resetWorkspaceSummaryQueries(client, "wiki-summaries", projectUID, data);
                }
            },
        }));
        events.forEach((event) => socket.on(event));
        const release = acquireWorkspaceSummaryTopics(
            socket,
            uids.map((uid) => [ESocketTopic.BoardWikiPrivate, uid] as const)
        );
        return () => {
            active = false;
            events.forEach((event) => socket.off(event));
            release();
        };
    }, [client, projectUID, authScope, privateKey]);
}
