import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Toast from "@/components/base/Toast";
import useDeleteSelectedWebhooks from "@/controllers/api/settings/webhooks/useDeleteSelectedWebhooks";
import useGetWebhooks from "@/controllers/api/settings/webhooks/useGetWebhooks";
import setupApiErrorHandler from "@/core/helpers/setupApiErrorHandler";
import { usePageNavigateRef } from "@/core/hooks/usePageNavigate";
import useRoleActionFilter from "@/core/hooks/useRoleActionFilter";
import { SettingRole } from "@/core/models/roles";
import { useAppSetting } from "@/core/providers/AppSettingProvider";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { ROUTES } from "@/core/routing/constants";
import WebhookList from "@/pages/SettingsPage/components/webhook/WebhookList";
import { EHttpStatus } from "@langboard/core/enums";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function WebhooksPage() {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const navigate = usePageNavigateRef();
    const { currentUser, isValidating, setIsValidating } = useAppSetting();
    const [selectedWebhooks, setSelectedWebhooks] = useState<string[]>([]);
    const { mutateAsync: getWebhooksMutateAsync } = useGetWebhooks();
    const { mutate: deleteSelectedWebhooksMutate } = useDeleteSelectedWebhooks();
    const settingRoleActions = currentUser.useField("setting_role_actions");
    const { hasRoleAction } = useRoleActionFilter(settingRoleActions);
    const canDeleteWebhook = hasRoleAction(SettingRole.EAction.WebhookDelete);

    useEffect(() => {
        setPageAliasRef.current("Webhooks");
        getWebhooksMutateAsync({});
    }, []);

    const openCreateDialog = () => {
        navigate(ROUTES.SETTINGS.CREATE_WEBHOOK);
    };

    const deleteSelectedWebhooks = () => {
        if (isValidating || !selectedWebhooks.length || !canDeleteWebhook) {
            return;
        }

        setIsValidating(true);

        deleteSelectedWebhooksMutate(
            {
                webhook_uids: selectedWebhooks,
            },
            {
                onSuccess: () => {
                    Toast.Add.success(t("successes.Selected webhooks deleted successfully."));
                    setSelectedWebhooks([]);
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
                <span className="w-36">{t("settings.Webhooks")}</span>
                <Flex gap="2" wrap justify="end" maxW={{ initial: "full", sm: "auto" }}>
                    {selectedWebhooks.length > 0 && canDeleteWebhook && (
                        <Button variant="destructive" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={deleteSelectedWebhooks}>
                            <IconComponent icon="trash" size="4" />
                            {t("common.Delete")}
                        </Button>
                    )}
                    {hasRoleAction(SettingRole.EAction.WebhookCreate) && (
                        <Button variant="outline" disabled={isValidating} className="gap-2 pl-2 pr-3" onClick={openCreateDialog}>
                            <IconComponent icon="plus" size="4" />
                            {t("settings.Add new")}
                        </Button>
                    )}
                </Flex>
            </Flex>
            <WebhookList selectedWebhooks={selectedWebhooks} setSelectedWebhooks={setSelectedWebhooks} />
        </>
    );
}

export default WebhooksPage;
