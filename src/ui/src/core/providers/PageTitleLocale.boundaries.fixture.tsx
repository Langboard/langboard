import { calls } from "./PageTitleLocale.stats.fixture";
const noop = () => {};
const user = { uid: "synthetic-user", useField: () => [] };
export const useAuth = () => ({ updatedUser: noop });
export const useAppSetting = () => ({ currentUser: user, isValidating: false, setIsValidating: noop });
export const useDashboard = () => ({ currentUser: user, socket: {} });
export const usePageNavigateRef = () => navigate;
const navigate = () => {
    calls.navigation++;
};
const mutation = () => {
    calls.mutation++;
};
const load = async () => {
    calls.load++;
    return [];
};
export const useGetGlobalLabels = () => ({ mutateAsync: load, isPending: false });
export const useSaveGlobalLabel = () => ({ mutateAsync: mutation, isPending: false });
const result = {
    data: { provider: "oidc", projects: [] },
    isFetching: false,
    isLoading: false,
    mutate: mutation,
    mutateAsync: mutation,
    refetch: load,
    hasRoleAction: () => false,
};
export default function boundary() {
    return result;
}
