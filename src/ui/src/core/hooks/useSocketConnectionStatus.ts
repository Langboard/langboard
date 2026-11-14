import { useSyncExternalStore } from "react";
import { getSocketConnectionStatus, subscribeSocketConnectionStatus } from "@/core/stores/socket/state";

export default function useSocketConnectionStatus() {
    return useSyncExternalStore(subscribeSocketConnectionStatus, getSocketConnectionStatus, getSocketConnectionStatus);
}
