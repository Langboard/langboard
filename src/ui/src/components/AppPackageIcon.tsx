import { Package } from "lucide-react";
import { useState } from "react";
import { API_URL } from "@/constants";

export interface PackageInstallation {
    kind: "official" | "manual";
    sha256: string;
    repository?: string;
    tag?: string;
    store?: { icon?: string; icon_dark?: string; screenshots?: string[] };
}

export function packageAsset(key: string, installation?: PackageInstallation | null, asset?: string) {
    if (
        !installation ||
        !/^[0-9a-f]{64}$/.test(installation.sha256) ||
        !asset ||
        !/^panel\/[A-Za-z0-9_./-]+$/.test(asset) ||
        asset.split("/").some((part) => part === ".." || part === ".")
    )
        return undefined;
    return `${API_URL}/apps/packages/${encodeURIComponent(key)}/${installation.sha256}/${asset}`;
}

export default function AppPackageIcon({
    appKey,
    installation,
    fallbackUrl,
    fallbackDarkUrl,
}: {
    appKey: string;
    installation?: PackageInstallation | null;
    fallbackUrl?: string;
    fallbackDarkUrl?: string;
}) {
    const [failed, setFailed] = useState<string | null>(null);
    const source = packageAsset(appKey, installation, installation?.store?.icon) ?? fallbackUrl;
    const darkSource = packageAsset(appKey, installation, installation?.store?.icon_dark) ?? fallbackDarkUrl;
    const image = (url: string | undefined, className: string) =>
        url && failed !== url ? (
            <img
                className={`size-full object-contain ${className}`}
                src={url}
                alt=""
                loading="lazy"
                decoding="async"
                onError={() => setFailed(url)}
            />
        ) : (
            <Package className={`size-7 text-muted-foreground ${className}`} aria-hidden="true" />
        );
    return (
        <span className="avatar flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-2xl border bg-card p-2 shadow-sm">
            {image(source, darkSource ? "dark:hidden" : "")}
            {darkSource && image(darkSource, "hidden dark:block")}
        </span>
    );
}
