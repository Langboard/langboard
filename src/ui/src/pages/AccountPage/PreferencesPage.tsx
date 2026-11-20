import { useTranslation } from "react-i18next";
import Box from "@/components/base/Box";
import PreferenceLanguage from "@/pages/AccountPage/components/preference/PreferenceLanguage";
import PreferenceNotification from "@/pages/AccountPage/components/preference/PreferenceNotification";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { useEffect } from "react";

function PreferencesPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();

    useEffect(() => {
        setPageAliasRef.current("Preferences");
    }, []);

    return (
        <>
            <h2 className="scroll-m-20 border-b pb-3 text-2xl font-semibold">{t("myAccount.Preferences")}</h2>
            <Box mt="8">
                <PreferenceNotification />
                <PreferenceLanguage />
            </Box>
        </>
    );
}

export default PreferencesPage;
