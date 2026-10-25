import BrandLoading from "@/components/BrandLoading";
import { normalizeLocale } from "@/core/utils/LocalePolicy";
import { createContext, useContext, useEffect, useRef } from "react";
import { Routing } from "@langboard/core/constants";
import { api, refresh } from "@/core/helpers/Api";
import { AuthUser } from "@/core/models";
import { ROUTES } from "@/core/routing/constants";
import { useTranslation } from "react-i18next";
import useAuthStore from "@/core/stores/AuthStore";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import Progress from "@/components/base/Progress";
import { useLocation } from "react-router";

export interface IAuthContext {
    signIn: (accessToken: string, redirectCallback?: () => void) => Promise<void>;
    updatedUser: () => void;
    signOut: () => Promise<void>;
    currentUser: AuthUser.TModel | null;
}

interface IAuthProviderProps {
    children: React.ReactNode;
}

const initialContext = {
    signIn: () => Promise.resolve(),
    updatedUser: () => {},
    signOut: async () => {},
    currentUser: null,
};

const AuthContext = createContext<IAuthContext>(initialContext);

export const AuthProvider = ({ children }: IAuthProviderProps): React.ReactNode => {
    const [_, i18n] = useTranslation();
    const { state, currentUser, pageLoaded, updateToken, removeToken } = useAuthStore();
    const navigate = usePageNavigateRef();
    const { pathname } = useLocation();
    const preferredLanguageUserRef = useRef<string | null>(null);

    useEffect(() => {
        if (state !== "loaded") return;
        if (!currentUser) {
            preferredLanguageUserRef.current = null;
            return;
        }
        if (preferredLanguageUserRef.current !== currentUser.uid) {
            if (currentUser.preferred_lang) i18n.changeLanguage(normalizeLocale(currentUser.preferred_lang));
            preferredLanguageUserRef.current = currentUser.uid;
        }
    }, [state, currentUser, i18n]);

    useEffect(() => {
        const shouldSkip =
            pathname.startsWith(ROUTES.SIGN_IN.EMAIL) ||
            pathname.startsWith(ROUTES.SIGN_IN.OIDC_CALLBACK) ||
            pathname.startsWith(ROUTES.SIGN_UP.ROUTE) ||
            pathname.startsWith(ROUTES.ACCOUNT_RECOVERY.NAME);

        switch (state) {
            case "initial":
                void refresh();
                return;
            case "loaded":
                if (!currentUser && !shouldSkip) {
                    navigate(ROUTES.SIGN_IN.EMAIL);
                }
                return;
        }
    }, [state, currentUser, pathname, navigate]);

    const updatedUser = () => {
        refresh();
    };

    const signIn = async (accessToken: string, redirectCallback?: () => void) => {
        await updateToken(accessToken, api);
        redirectCallback?.();
    };

    const signOut = async () => {
        try {
            await api.post(Routing.API.AUTH.SIGN_OUT);
        } finally {
            removeToken();
        }
    };

    return (
        <AuthContext.Provider
            value={{
                signIn,
                updatedUser,
                signOut,
                currentUser,
            }}
        >
            {state !== "loaded" ? <BrandLoading /> : !pageLoaded && <Progress indeterminate height="1" className="fixed top-0 z-[9999999]" />}
            {state === "loaded" ? children : null}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error("useAuth must be used within an AuthProvider");
    }
    return context;
};
