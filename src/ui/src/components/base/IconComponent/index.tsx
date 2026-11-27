/* eslint-disable @typescript-eslint/no-explicit-any */
import { icons } from "lucide-react";
import { InternalIcon } from "@/assets/svgs/index";
import React, { forwardRef, memo } from "react";
import Flag from "react-flagkit";
import { VariantProps, tv } from "tailwind-variants";
import SuspenseComponent from "@/components/base/SuspenseComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { DimensionMap } from "@/core/utils/VariantUtils";
import { Utils } from "@langboard/core/utils";

export const IconVariants = tv(
    {
        variants: {
            size: DimensionMap.all,
        },
    },
    {
        responsiveVariants: true,
    }
);

type TSVGElementAttributes = React.RefAttributes<SVGSVGElement> & Partial<React.SVGProps<SVGSVGElement>>;
type TImageElementAttributes = React.RefAttributes<HTMLImageElement> & React.HTMLAttributes<HTMLImageElement>;

export interface ICountryIconProps extends TImageElementAttributes, VariantProps<typeof IconVariants> {
    icon: `country-${string}`;
}

export interface ILucideIconProps extends TSVGElementAttributes, VariantProps<typeof IconVariants> {
    icon: keyof typeof icons | (string & {});
}

export type TIconProps = React.ForwardRefExoticComponent<ICountryIconProps> | React.ForwardRefExoticComponent<ILucideIconProps>;

export type TIconName = ICountryIconProps["icon"] | ILucideIconProps["icon"];

const IconComponent = memo(
    forwardRef<React.ComponentRef<TIconProps>, React.ComponentPropsWithoutRef<TIconProps>>(({ icon, size, className, id, ...props }, ref) => {
        if (size) {
            className = cn(IconVariants({ size }), className ?? "");
        }

        const isCountryIcon = (name: string): name is `country-${string}` => name.startsWith("country-");

        if (isCountryIcon(icon)) {
            const country = icon.split("country-").pop();

            return (
                <SuspenseComponent className={className}>
                    <Flag country={country} className={className} id={id} {...(props as React.HTMLAttributes<HTMLImageElement>)} />
                </SuspenseComponent>
            );
        }

        const pascalCaseIcon = new Utils.String.Case(icon).toPascal();
        const TargetIcon: React.ElementType | undefined = icons[pascalCaseIcon as keyof typeof icons];

        if (!TargetIcon) {
            return (
                <SuspenseComponent className={className}>
                    <InternalIcon icon={pascalCaseIcon} className={className} ref={ref as any} {...(props as any)} />
                </SuspenseComponent>
            );
        }

        return (
            <SuspenseComponent className={className}>
                <TargetIcon className={className} ref={ref} id={id} {...props} />
            </SuspenseComponent>
        );
    })
);

export default IconComponent;
