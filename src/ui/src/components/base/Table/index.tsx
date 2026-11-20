import * as React from "react";
import { cn } from "@/core/utils/ComponentUtils";
import Tooltip from "@/components/base/Tooltip";
import Flex, { IFlexProps } from "@/components/base/Flex";
import Box from "@/components/base/Box";

const Root = React.forwardRef<HTMLTableElement, React.HTMLAttributes<HTMLTableElement> & { tableOnly?: bool }>(
    ({ className, tableOnly, ...props }, ref) => {
        const elem = <table ref={ref} className={cn("w-full caption-bottom text-sm tabular-nums", className)} {...props} />;

        if (tableOnly) {
            return elem;
        }

        return <div className="relative w-full overflow-auto">{elem}</div>;
    }
);
Root.displayName = "Table";

const FlexRoot = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <Flex ref={ref} direction="col" className={cn("relative w-full text-sm", className)} {...props} />
));
FlexRoot.displayName = "TableFlexRoot";

const Header = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(({ className, ...props }, ref) => (
    <thead ref={ref} className={cn("bg-muted/40 [&_tr]:border-b", className)} {...props} />
));
Header.displayName = "TableHeader";

const FlexHeader = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <Flex ref={ref} direction="col" className={cn("border-b", className)} {...props} />
));
FlexHeader.displayName = "TableFlexHeader";

const Body = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(({ className, ...props }, ref) => (
    <tbody ref={ref} className={cn("[&_tr:last-child]:border-0", className)} {...props} />
));
Body.displayName = "TableBody";

const FlexBody = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <Flex ref={ref} direction="col" className={cn("[&>div:last-child]:border-b", className)} {...props} />
));
FlexBody.displayName = "TableFlexBody";

const Footer = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(({ className, ...props }, ref) => (
    <tfoot ref={ref} className={cn("border-t bg-muted/50 font-medium [&>tr]:last:border-b-0", className)} {...props} />
));
Footer.displayName = "TableFooter";

const FlexFooter = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <Flex ref={ref} className={cn("border-t bg-muted/50 font-medium [&>div]:last:border-b-0", className)} {...props} />
));
FlexFooter.displayName = "TableFlexFooter";

const Row = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(({ className, ...props }, ref) => (
    <tr
        ref={ref}
        className={cn("border-b transition-colors focus-within:bg-muted/50 hover:bg-muted/40 data-[state=selected]:bg-primary/10", className)}
        {...props}
    />
));
Row.displayName = "TableRow";

const FlexRow = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <Flex
        ref={ref}
        className={cn("border-b transition-colors focus-within:bg-muted/50 hover:bg-muted/40 data-[state=selected]:bg-primary/10", className)}
        {...props}
    />
));
FlexRow.displayName = "TableFlexRow";

interface IBaseTableCellProps {
    title?: string;
    titleAlign?: "center" | "start" | "end";
    titleSide?: "top" | "bottom" | "left" | "right";
}

export interface ITableCellProps extends React.ThHTMLAttributes<HTMLTableCellElement>, IBaseTableCellProps {}

const Head = React.forwardRef<HTMLTableCellElement, ITableCellProps>(({ title, titleAlign, titleSide, className, children, ...props }, ref) => {
    const headProps = {
        ref,
        className: cn("h-10 px-4 text-left align-middle font-medium text-muted-foreground [&:has([role=checkbox])]:pr-0", className),
        ...props,
    };

    if (!title) {
        return <th {...headProps}>{children}</th>;
    }

    return (
        <th {...headProps}>
            <Tooltip.Root>
                <Tooltip.Trigger>{children}</Tooltip.Trigger>
                <Tooltip.Content align={titleAlign} side={titleSide}>
                    {title}
                </Tooltip.Content>
            </Tooltip.Root>
        </th>
    );
});
Head.displayName = "TableHead";

const Cell = React.forwardRef<HTMLTableCellElement, ITableCellProps>(({ title, titleAlign, titleSide, className, children, ...props }, ref) => {
    const cellProps = {
        ref,
        className: cn("px-4 py-3 align-middle [&:has([role=checkbox])]:pr-0", className),
        ...props,
    };

    if (!title) {
        return <td {...cellProps}>{children}</td>;
    }

    return (
        <td {...cellProps}>
            <Tooltip.Root>
                <Tooltip.Trigger>{children}</Tooltip.Trigger>
                <Tooltip.Content align={titleAlign} side={titleSide}>
                    {title}
                </Tooltip.Content>
            </Tooltip.Root>
        </td>
    );
});
Cell.displayName = "TableCell";

export interface ITableCellFlexProps extends IFlexProps, IBaseTableCellProps {}

const FlexHead = React.forwardRef<HTMLDivElement, ITableCellFlexProps>(({ title, titleAlign, titleSide, className, children, ...props }, ref) => {
    const headProps = {
        ref,
        className: cn("min-h-10 px-4 py-2 text-left font-medium text-muted-foreground truncate", className),
        ...props,
    };

    if (!title) {
        return <Box {...headProps}>{children}</Box>;
    }

    return (
        <Box {...headProps}>
            <Tooltip.Root>
                <Tooltip.Trigger>{children}</Tooltip.Trigger>
                <Tooltip.Content align={titleAlign} side={titleSide}>
                    {title}
                </Tooltip.Content>
            </Tooltip.Root>
        </Box>
    );
});
FlexHead.displayName = "TableFlexHead";

const FlexCell = React.forwardRef<HTMLDivElement, ITableCellFlexProps>(({ title, titleAlign, titleSide, className, children, ...props }, ref) => {
    const cellProps = {
        ref,
        className: cn("px-4 py-3 align-middle truncate", className),
        ...props,
    };

    if (!title) {
        return <Box {...cellProps}>{children}</Box>;
    }

    return (
        <Box {...cellProps}>
            <Tooltip.Root>
                <Tooltip.Trigger>{children}</Tooltip.Trigger>
                <Tooltip.Content align={titleAlign} side={titleSide}>
                    {title}
                </Tooltip.Content>
            </Tooltip.Root>
        </Box>
    );
});
FlexCell.displayName = "TableFlexCell";

const Caption = React.forwardRef<HTMLTableCaptionElement, React.HTMLAttributes<HTMLTableCaptionElement>>(({ className, ...props }, ref) => (
    <caption ref={ref} className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />
));
Caption.displayName = "TableCaption";

const FlexCaption = React.forwardRef<HTMLDivElement, IFlexProps>(({ className, ...props }, ref) => (
    <FlexRoot ref={ref} className={cn("mt-4 text-sm text-muted-foreground", className)} {...props} />
));
FlexCaption.displayName = "TableFlexCaption";

export default {
    Body,
    Caption,
    Cell,
    Footer,
    Head,
    Header,
    Root,
    Row,
    FlexRoot,
    FlexHeader,
    FlexBody,
    FlexFooter,
    FlexRow,
    FlexHead,
    FlexCell,
    FlexCaption,
};
