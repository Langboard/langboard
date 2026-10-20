import logo from "@/assets/svgs/LangboardLoading.svg";

export default function BrandLoading() {
    return (
        <div
            role="status"
            aria-label="Loading Langboard"
            aria-busy="true"
            className="fixed inset-0 z-[9999999] grid place-items-center bg-background"
        >
            <img src={logo} alt="" width="96" height="96" />
        </div>
    );
}
