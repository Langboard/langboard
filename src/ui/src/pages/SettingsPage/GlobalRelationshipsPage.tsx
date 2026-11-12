import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useDeleteSelectedGlobalRelationships from "@/controllers/api/settings/relationships/useDeleteSelectedGlobalRelationships";
import useGetGlobalRelationships from "@/controllers/api/settings/relationships/useGetGlobalRelationships";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import GlobalRelationshipExport from "@/pages/SettingsPage/components/relationships/GlobalRelationshipExport";
import GlobalRelationshipImport from "@/pages/SettingsPage/components/relationships/GlobalRelationshipImport";
import GlobalRelationshipList from "@/pages/SettingsPage/components/relationships/GlobalRelationshipList";
import { EHttpStatus } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function GlobalRelationshipsPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const [selectedGlobalRelationships, setSelectedGlobalRelationships] = useState<string[]>([]);
    const { mutateAsync: getGlobalRelationshipsMutateAsync } = useGetGlobalRelationships();
    const { mutate: deleteSelectedGlobalRelationshipsMutate } = useDeleteSelectedGlobalRelationships();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canDeleteGlobalRelationship = hasRoleAction(SettingRole.EAction.GlobalRelationshipDelete);

    useEffect(() => {
        setPageAliasRef.current("Global relationships");
        getGlobalRelationshipsMutateAsync({});
    }, []);

    const deleteSelectedGlobalRelationships = () => {
        if (isValidating || !selectedGlobalRelationships.length || !canDeleteGlobalRelationship) {
            return;
        }

        setIsValidating(true);

        deleteSelectedGlobalRelationshipsMutate(
            {
                relationship_type_uids: selectedGlobalRelationships,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected global relationship types deleted successfully."));
                    setSelectedGlobalRelationships([]);
                },
                onError: (error) => {
                    const { handle } = setupApiErrorHandler({
                        [EHttpStatus.HTTP_403_FORBIDDEN]: {
                            after: () => navigate(ROUTES.ERROR(EHttpStatus.HTTP_403_FORBIDDEN), { replace: true }),
                        },
                    });

                    handle(error);
                },
                onSettled: () => {
                    setIsValidating(false);
                },
            }
        );
    };

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_GLOBAL_RELATIONSHIP);
    };

    return (
        <>
            <Flex
                justify={{ sm: "between" }}
                direction={{ initial: "col", sm: "row" }}
                gap="2"
                mb="4"
                pb="2"
                textSize="2xl"
                weight="semibold"
                className="scroll-m-20 flex-wrap gap-3"
            >
                <span className="max-w-72 truncate">{t("settings.Global relationships")}</span>
                <Flex gap="2" wrap justify="end" maxW={{ initial: "full", sm: "96" }}>
                    {selectedGlobalRelationships.length > 0 && canDeleteGlobalRelationship && (
                        <Button variant="destructive" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={deleteSelectedGlobalRelationships}>
                            <IconComponent icon="trash" size="4" />
                            {t("common.Delete")}
                        </Button>
                    )}
                    {hasRoleAction(SettingRole.EAction.GlobalRelationshipCreate) && <GlobalRelationshipImport />}
                    <GlobalRelationshipExport />
                    {hasRoleAction(SettingRole.EAction.GlobalRelationshipCreate) && (
                        <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                            <IconComponent icon="plus" size="4" />
                            {t("settings.Add new")}
                        </Button>
                    )}
                </Flex>
            </Flex>
            <GlobalRelationshipList
                selectedGlobalRelationships={selectedGlobalRelationships}
                setSelectedGlobalRelationships={setSelectedGlobalRelationships}
            />
        </>
    );
}

export default GlobalRelationshipsPage;
