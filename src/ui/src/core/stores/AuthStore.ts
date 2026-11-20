import { Routing } from "@langboard/core/constants";
import { AuthUser, BotModel } from "@/core/models";
import { cleanModels } from "@/core/models/Base";
import { queryClient } from "@/core/helpers/QueryClient";
import useSocketStore from "@/core/stores/SocketStore";
import { AxiosInstance } from "axios";
import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

type TOidcCallbackRequestStatus = "pending" | "done";

interface IAuthStore {
    state: "initial" | "pending" | "loaded";
    currentUser: AuthUser.TModel | null;
    pageLoaded: bool;
    hydratedSessionVersion: number;
    getToken: () => string | null;
    getSessionVersion: () => number;
    getIdentityVersion: () => number;
    updateToken: (token: string, api: AxiosInstance) => Promise<void>;
    removeToken: () => void;
    getOidcCallbackRequestStatus: (requestKey: string) => TOidcCallbackRequestStatus | null;
    setOidcCallbackRequestStatus: (requestKey: string, status: TOidcCallbackRequestStatus) => void;
    removeOidcCallbackRequestStatus: (requestKey: string) => void;
}

let accessToken: string | null = null;
let tokenUpdateVersion = 0;
let identityVersion = 0;

const clearIdentityState = () => {
    identityVersion += 1;
    useSocketStore.getState().close();
    queryClient.clear();
    cleanModels();
};

const useAuthStore = create(
    immer<IAuthStore>((set, get) => {
        return {
            state: "initial",
            currentUser: null,
            pageLoaded: false,
            hydratedSessionVersion: 0,
            getToken: () => accessToken,
            getSessionVersion: () => tokenUpdateVersion,
            getIdentityVersion: () => identityVersion,
            updateToken: async (token: string, api: AxiosInstance) => {
                if (get().state === "pending") {
                    return;
                }

                accessToken = token;
                tokenUpdateVersion += 1;
                const sessionVersion = tokenUpdateVersion;
                // Notify session selectors before identity hydration can leave prior privileged data visible.
                set((state) => ({ ...state }));

                const tryGetUser = async () => {
                    const MAX_ATTEMPTS = 5;
                    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt += 1) {
                        if (sessionVersion !== tokenUpdateVersion) return undefined;
                        try {
                            const response = await api.get<{
                                user: AuthUser.Interface;
                                bots: BotModel.Interface[];
                            }>(Routing.API.AUTH.ABOUT_ME, {
                                headers: {
                                    Authorization: `Bearer ${accessToken}`,
                                },
                                withCredentials: true,
                            });

                            if (!response) {
                                throw new Error();
                            }

                            return response.data;
                        } catch {
                            if (sessionVersion !== tokenUpdateVersion) return undefined;
                            if (attempt === MAX_ATTEMPTS - 1) {
                                return undefined;
                            }

                            await new Promise((resolve) => setTimeout(resolve, 1000));
                        }
                    }

                    return undefined;
                };

                const data = await tryGetUser();
                // Logout or another login owns the current identity, even if this request succeeds.
                if (sessionVersion !== tokenUpdateVersion) return;
                if (!data) {
                    clearIdentityState();
                    set({ currentUser: null, state: "loaded" });
                    return;
                }

                const previousUser = get().currentUser;
                if (previousUser && previousUser.uid !== data.user.uid) {
                    clearIdentityState();
                }
                const user = AuthUser.Model.fromOne(data.user);
                BotModel.Model.fromArray(data.bots, true);

                set({ currentUser: user, state: "loaded", hydratedSessionVersion: sessionVersion });
            },
            removeToken: () => {
                clearIdentityState();
                accessToken = null;
                tokenUpdateVersion += 1;
                set({ currentUser: null, state: "loaded" });
            },
            getOidcCallbackRequestStatus: (requestKey) => {
                const status = sessionStorage.getItem(requestKey);
                return status === "pending" || status === "done" ? status : null;
            },
            setOidcCallbackRequestStatus: (requestKey, status) => {
                sessionStorage.setItem(requestKey, status);
            },
            removeOidcCallbackRequestStatus: (requestKey) => {
                sessionStorage.removeItem(requestKey);
            },
        };
    })
);

export const getAuthStore = () => useAuthStore.getState();

export default useAuthStore;
