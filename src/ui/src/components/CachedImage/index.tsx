import { forwardRef, memo, useEffect, useState } from "react";
import { VariantProps, tv } from "tailwind-variants";
import useProtectedImage from "@/core/hooks/useProtectedImage";
import Skeleton from "@/components/base/Skeleton";
import SuspenseComponent from "@/components/base/SuspenseComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { DimensionMap } from "@/core/utils/VariantUtils";

const CachedImageVariants = tv(
    {
        variants: {
            size: DimensionMap.all,
            w: DimensionMap.width,
            h: DimensionMap.height,
        },
        defaultVariants: {
            size: undefined,
            width: undefined,
            height: undefined,
        },
    },
    {
        responsiveVariants: true,
    }
);

interface ICachedImageProps extends React.ImgHTMLAttributes<HTMLImageElement>, VariantProps<typeof CachedImageVariants> {
    fallback?: React.ReactNode;
}

const imageCache = new Map<string, HTMLImageElement>();

const lazyImage = (src: string, cache = true) => {
    return new Promise((resolve, reject) => {
        if (cache && imageCache.has(src)) {
            resolve(imageCache.get(src));
            return;
        }

        const image = new Image();
        image.onload = () => {
            if (cache) imageCache.set(src, image);
            resolve(image);
        };
        image.onerror = reject;
        image.src = src;
    });
};

const CachedImage = memo(
    forwardRef<HTMLImageElement, ICachedImageProps>(({ size, w, h, src, fallback, className, ...props }, ref) => {
        const resolved = useProtectedImage(src);
        const imageSrc = resolved.src;
        const [image, setImage] = useState<{ src: string; protected: boolean; status: "ready" | "error" } | null>(null);

        const classNames = cn(CachedImageVariants({ size, w, h }), className);

        useEffect(() => {
            let disposed = false;
            setImage(null);
            if (!imageSrc) return;

            lazyImage(imageSrc, !resolved.protected)
                .then(() => {
                    if (disposed) return;
                    setImage({ src: imageSrc, protected: resolved.protected, status: "ready" });
                })
                .catch(() => {
                    if (!disposed) setImage({ src: imageSrc, protected: resolved.protected, status: "error" });
                });
            return () => {
                disposed = true;
            };
        }, [imageSrc, resolved.protected]);

        const currentImage = image && image.src === imageSrc && image.protected === resolved.protected ? image : null;
        return (
            <SuspenseComponent className={classNames}>
                {!imageSrc ? (
                    (fallback ?? null)
                ) : currentImage?.status === "ready" ? (
                    <img ref={ref} className={classNames} {...props} src={imageSrc} srcSet={resolved.protected ? undefined : props.srcSet} />
                ) : currentImage?.status === "error" ? (
                    (fallback ?? <Skeleton className={classNames} />)
                ) : null}
            </SuspenseComponent>
        );
    })
);

export default CachedImage;
