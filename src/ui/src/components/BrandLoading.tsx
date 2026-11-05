import logo from "@/assets/svgs/LangboardLoading.svg";
import { useTranslation } from "react-i18next";

export default function BrandLoading() {
    const [t] = useTranslation(undefined, { useSuspense: false });
    return (
        <div
            role="status"
            aria-label={t("common.Loading...", { defaultValue: "Loading..." })}
            aria-busy="true"
            className="fixed inset-0 z-[9999999] grid place-items-center bg-background"
        >
            <img src={logo} alt="" width="96" height="96" />
        </div>
    );
}
