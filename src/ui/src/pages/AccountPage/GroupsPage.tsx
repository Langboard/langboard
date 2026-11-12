import { useTranslation } from "react-i18next";
import AccountUserGroupList, { SkeletonAccountUserGroupList } from "@/pages/AccountPage/components/group/AccountUserGroupList";
import { usePageHeader } from "@/core/providers/PageHeaderProvider";
import { useEffect } from "react";

export function SkeletonGroupsPage(): React.JSX.Element {
    const [t] = useTranslation();

    return (
        <>
            <h2 className="mb-11 scroll-m-20 border-b pb-3 text-2xl font-semibold">{t("myAccount.Groups")}</h2>
            <SkeletonAccountUserGroupList />
        </>
    );
}

function GroupsPage(): React.JSX.Element {
    const { setPageAliasRef } = usePageHeader();
    const [t] = useTranslation();

    useEffect(() => {
        setPageAliasRef.current("Groups");
    }, []);

    return (
        <>
            <h2 className="mb-11 scroll-m-20 border-b pb-3 text-2xl font-semibold">{t("myAccount.Groups")}</h2>
            <AccountUserGroupList />
        </>
    );
}

export default GroupsPage;
