import Flex from "@/components/base/Flex";
import useGetOllamaModelList from "@/controllers/api/settings/ollama/useGetOllamaModelList";
import useGetOllamaRunningModelList from "@/controllers/api/settings/ollama/useGetOllamaRunningModelList";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { useSocket } from "@/core/providers/SocketProvider";
import OllamaModelList from "@/pages/SettingsPage/components/ollama/OllamaModelList";
import OllamaModelTrackingList from "@/pages/SettingsPage/components/ollama/OllamaModelTrackingList";
import OllamaPullModelButton from "@/pages/SettingsPage/components/ollama/OllamaPullModelButton";
import { ESocketTopic, GLOBAL_TOPIC_ID } from "@langboard/core/enums";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

function OllamaPage() {
    const socket = useSocket();
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();
    const { mutateAsync: getOllamaModelListMutateAsync } = useGetOllamaModelList();
    const { mutateAsync: getOllamaRunningModelListMutateAsync } = useGetOllamaRunningModelList();

    useEffect(() => {
        setPageAliasRef.current("Ollama");
        let isMounted = true;
        let isSubscribed = false;

        const fetchModels = async () => {
            await getOllamaModelListMutateAsync({});
            await getOllamaRunningModelListMutateAsync({});
            if (!isMounted) {
                return;
            }

            socket.subscribe(ESocketTopic.OllamaManager, [GLOBAL_TOPIC_ID]);
            isSubscribed = true;
        };

        fetchModels();

        return () => {
            isMounted = false;
            if (isSubscribed) {
                socket.unsubscribe(ESocketTopic.OllamaManager, [GLOBAL_TOPIC_ID]);
            }
        };
    }, []);

    return (
        <>
            <Flex justify="between" mb="4" pb="2" textSize="3xl" weight="semibold" className="scroll-m-20 border-b tracking-tight">
                <span className="w-36">{t("settings.Ollama")}</span>
            </Flex>
            <OllamaModelList />
            <OllamaModelTrackingList />
            <OllamaPullModelButton />
        </>
    );
}
OllamaPage.displayName = "OllamaPage";

export default OllamaPage;
