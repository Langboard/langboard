import { defineConfig, mergeConfig, type UserConfigFnObject } from "vite";
import base from "./vite.card-load.config";
import path from "node:path";

// Synthetic authentication/data boundaries; page components, title provider and locale resources are production modules.
const boundaries = new Set([
    "@/core/providers/AuthProvider",
    "@/controllers/api/account/useVerifyNewEmail",
    "@/core/providers/AppSettingProvider",
    "@/core/providers/DashboardProvider",
    "@/controllers/api/dashboard/useGetProjects",
    "@/controllers/socket/user/useUserProjectAssignedHandlers",
    "@/core/hooks/usePageNavigate",
    "@/core/hooks/useRoleActionFilter",
    "@/controllers/api/auth/useAuthEmail",
    "@/controllers/api/auth/useGetAuthProvider",
    "@/controllers/api/auth/useOidcLogin",
    "@/controllers/api/settings/apiKeys/useDeleteSelectedApiKeys",
    "@/controllers/api/settings/globalLabels/useGlobalLabels",
]);
const leaves = new Set([
    "@/pages/auth/SignInPage/EmailForm",
    "@/pages/auth/SignInPage/PasswordForm",
    "@/pages/AccountPage/components/preference/PreferenceLanguage",
    "@/pages/AccountPage/components/preference/PreferenceNotification",
    "@/pages/SettingsPage/components/apiKeys/ApiKeyList",
    "@/pages/DashboardPage/components/ProjectDiscoveryPage",
]);
export default defineConfig((env) =>
    mergeConfig((base as UserConfigFnObject)(env), {
        resolve: {
            alias: [
                {
                    find: "@/core/hooks/useSwitchSocketHandlers",
                    replacement: path.resolve("src/core/providers/PageTitleLocale.subscription.fixture.ts"),
                },
                ...[...boundaries].map((find) => ({
                    find,
                    replacement: path.resolve("src/core/providers/PageTitleLocale.boundaries.fixture.tsx"),
                })),
                ...[...leaves].map((find) => ({ find, replacement: path.resolve("src/core/providers/PageTitleLocale.leaf.fixture.tsx") })),
            ],
        },
        optimizeDeps: { entries: ["src/core/providers/PageTitleLocale.fixture.html"] },
        cacheDir: "node_modules/.vite-page-title-locale",
    })
);
