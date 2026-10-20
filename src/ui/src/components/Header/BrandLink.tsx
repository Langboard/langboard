import { useId, useMemo, useRef } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import logo from "@/assets/svgs/Langboard.svg?raw";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";

function BrandLink({ className, onClick }: { className?: string; onClick?: () => void }) {
    const [t] = useTranslation();
    const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
    const image = useRef<HTMLSpanElement>(null);
    const runningUntil = useRef(0);
    const markup = useMemo(() => {
        // Each header/drawer instance needs its own paint, mask and SMIL IDs.
        const prefix = `brand-${id}-`;
        const ids = [...logo.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
        let svg = logo.replace(/aria-labelledby="title desc"/, `aria-labelledby="${prefix}title ${prefix}desc"`);
        for (const name of ids.sort((a, b) => b.length - a.length)) {
            svg = svg
                .replaceAll(`id="${name}"`, `id="${prefix}${name}"`)
                .replaceAll(`#${name}`, `#${prefix}${name}`)
                .replaceAll(`begin="${name}.`, `begin="${prefix}${name}.`);
        }
        return svg.replace(/(width|height)="1024"/g, (attribute) => attribute.replace("1024", "24"));
    }, [id]);

    const play = () => {
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || performance.now() < runningUntil.current) return;
        const motion = image.current?.querySelector<SVGAnimationElement>("animateMotion");
        if (motion) {
            motion.beginElement();
            runningUntil.current = performance.now() + 1800;
        }
    };

    return (
        <Link
            to={ROUTES.DASHBOARD.PROJECTS.ALL}
            viewTransition
            aria-label={t("common.Go to Dashboard")}
            className={cn(
                "inline-flex shrink-0 items-center gap-2 rounded-sm font-semibold",
                "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4",
                className
            )}
            onPointerEnter={play}
            onFocus={play}
            onClick={onClick}
        >
            <span ref={image} aria-hidden="true" className="block size-6 shrink-0" dangerouslySetInnerHTML={{ __html: markup }} />
            <span>Langboard</span>
        </Link>
    );
}

export default BrandLink;
