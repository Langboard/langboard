import { useAuth } from "@/core/providers/AuthProvider";
import AppStore from "./AppStore";

export default function AppsPage() {
    const { currentUser } = useAuth();
    return currentUser ? <AppStore userUID={currentUser.uid} /> : null;
}
