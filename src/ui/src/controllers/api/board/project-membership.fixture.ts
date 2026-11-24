import "@/core/injection";
import { AuthUser, Project } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import useSocketStore from "@/core/stores/SocketStore";
import { subscribedCallback } from "@/core/stores/socket/registry";
import { ESocketTopic } from "@langboard/core/enums";
import { SocketEvents } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";

const scenario = new URLSearchParams(location.search).get("scenario");
const projectUID = "membership-fixture";
const date = new Date();
const user = AuthUser.Model.fromOne({ uid: "current-user", created_at: date, updated_at: date });
useAuthStore.setState({ currentUser: scenario === "signed-out" ? null : user });
Project.Model.fromOne({ uid: projectUID, owner_uid: scenario === "owner" ? user.uid : "other-owner", created_at: date, updated_at: date });
subscribedCallback(ESocketTopic.Dashboard, [projectUID]);
const event = Utils.String.format(SocketEvents.SERVER.DASHBOARD.PROJECT.ASSIGNED_USERS_UPDATED, { uid: projectUID });
document.querySelector("button")!.onclick = () => {
    const listeners = useSocketStore.getState().getStore().subscriptions[ESocketTopic.Dashboard]?.[projectUID]?.[event] ?? {};
    if (Object.values(listeners).flat().length !== 1) throw new Error("Expected one native membership handler");
    const payload = { assigned_members: scenario === "assigned" ? [{ uid: user.uid }] : [] };
    for (const listener of Object.values(listeners).flat()) listener(payload);
    document.querySelector("output")!.textContent = Project.Model.getModel(projectUID) ? "Project retained" : "Project removed";
};
