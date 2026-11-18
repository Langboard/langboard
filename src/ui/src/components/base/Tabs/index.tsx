/* eslint-disable @/max-len */
"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { type VariantProps, tv } from "tailwind-variants";
import { cn } from "@/core/utils/ComponentUtils";
import { composeRefs } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";

const TabsVariants = tv(
    {
        base: "relative inline-flex items-center justify-center rounded-lg transition-all duration-300 w-full",
        variants: {
            variant: {
                default: "bg-background border border-border",
                ghost: "bg-transparent",
                underline: "bg-transparent border-b border-border rounded-none",
            },
            size: {
                sm: "h-9 p-1",
                default: "h-10 p-1.5",
                lg: "h-12 p-2",
            },
        },
        defaultVariants: {
            variant: "default",
            size: "default",
        },
    },
    {
        responsiveVariants: true,
    }
);

export interface ITabsContext {
    activeValue?: string;
    activeTabBounds: { left: number; width: number };
    tabListRef: React.RefObject<HTMLDivElement | null>;
    setActiveValue: React.Dispatch<React.SetStateAction<string | undefined>>;
    setActiveTabBounds: React.Dispatch<React.SetStateAction<{ left: number; width: number }>>;
    setTabRef: (id: string, element: HTMLButtonElement | null) => void;
    handleTabClick: (value: string) => void;
    updateUI: React.DispatchWithoutAction;
}

interface IProviderProps {
    defaultValue?: string;
    value?: string;
    onValueChange?: (value: string) => void;
    children: React.ReactNode;
}

const initialContext = {
    activeTabBounds: { left: 0, width: 0 },
    tabListRef: { current: null },
    setActiveValue: () => {},
    setActiveTabBounds: () => {},
    setTabRef: () => {},
    handleTabClick: () => {},
    updateUI: () => {},
};

const TabsContext = React.createContext<ITabsContext>(initialContext);

const Provider = ({ defaultValue, value, onValueChange, children }: IProviderProps): React.ReactNode => {
    const [activeValue, setActiveValue] = React.useState(value || defaultValue);
    const [activeTabBounds, setActiveTabBounds] = React.useState({
        left: 0,
        width: 0,
    });
    const tabListRef = React.useRef<HTMLDivElement | null>(null);
    const tabRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});
    const [uiUpdated, updateUI] = React.useReducer((x) => x + 1, 0);

    const setTabRef = (value: string, element: HTMLButtonElement | null) => {
        tabRefs.current[value] = element;
    };

    React.useEffect(() => {
        if (value !== undefined) {
            setActiveValue(value);
        }
    }, [value]);

    React.useEffect(() => {
        if (!activeValue || !tabListRef.current) {
            return;
        }

        const list = tabListRef.current;
        const updateBounds = () => {
            if (tabListRef.current !== list) return;
            const activeTab = tabRefs.current[activeValue];
            if (activeTab) {
                const tabRect = activeTab.getBoundingClientRect();
                const containerRect = list.getBoundingClientRect();
                const left = tabRect.left - containerRect.left;
                const width = tabRect.width;
                setActiveTabBounds((previous) => (previous.left === left && previous.width === width ? previous : { left, width }));
            } else {
                setActiveTabBounds((previous) => (previous.width === 0 ? previous : { left: 0, width: 0 }));
                setActiveValue(undefined);
            }
        };
        const timer = setTimeout(updateBounds, 150);
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateBounds);
        observer?.observe(list);
        const activeTab = tabRefs.current[activeValue];
        if (activeTab) observer?.observe(activeTab);
        if (!observer) window.addEventListener("resize", updateBounds);
        return () => {
            clearTimeout(timer);
            observer?.disconnect();
            if (!observer) window.removeEventListener("resize", updateBounds);
        };
    }, [activeValue, uiUpdated]);

    const handleTabClick = (value: string) => {
        setActiveValue(value);
        onValueChange?.(value);
    };

    return (
        <TabsContext.Provider
            value={{
                activeValue,
                activeTabBounds,
                tabListRef,
                setActiveValue,
                setActiveTabBounds,
                setTabRef,
                handleTabClick,
                updateUI,
            }}
        >
            {children}
        </TabsContext.Provider>
    );
};
Provider.displayName = "TabsProvider";

const useTabsContext = () => {
    const context = React.useContext(TabsContext);
    if (!context) {
        throw new Error("useTabsContext must be used within a TabsProvider");
    }
    return context;
};

const List = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof TabsVariants>>(
    ({ className, variant, size, children, ...props }, ref) => {
        const { activeTabBounds, tabListRef } = useTabsContext();

        return (
            <div ref={composeRefs(tabListRef, ref)} className={cn(TabsVariants({ variant, size }), className)} {...props}>
                {" "}
                {/* Animated indicator */}
                <motion.div
                    className={cn(
                        "absolute z-10",
                        variant === "underline" ? "bottom-0 h-0.5 rounded-none bg-foreground" : "bottom-1 top-1 rounded-md bg-accent"
                    )}
                    initial={false}
                    animate={{
                        left: activeTabBounds.left,
                        width: activeTabBounds.width,
                    }}
                    transition={{
                        type: "spring",
                        stiffness: 400,
                        damping: 30,
                    }}
                />
                {/* Tab triggers */}
                {children}
            </div>
        );
    }
);
List.displayName = "TabsList";

const TabTriggerVariants = tv(
    {
        base: "relative inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-all duration-300 cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 flex-1",
        variants: {
            variant: {
                default: "text-muted-foreground hover:text-foreground data-[state=active]:text-primary-foreground",
                ghost: "text-muted-foreground hover:text-foreground hover:bg-accent data-[state=active]:text-primary-foreground data-[state=active]:bg-transparent",
                underline: "text-muted-foreground hover:text-foreground data-[state=active]:text-accent-foreground rounded-none",
            },
            size: {
                sm: "px-2.5 py-1 text-xs",
                default: "px-3 py-1.5 text-sm",
                lg: "px-4 py-2 text-base",
            },
        },
        defaultVariants: {
            variant: "default",
            size: "default",
        },
    },
    {
        responsiveVariants: true,
    }
);

export interface ITabTrigger extends React.HTMLAttributes<HTMLButtonElement>, VariantProps<typeof TabTriggerVariants> {
    value: string;
    disabled?: bool;
}

const Trigger = React.forwardRef<HTMLButtonElement, ITabTrigger>(({ value, className, variant, size, children, ...props }, ref) => {
    const { activeValue, setTabRef, handleTabClick } = useTabsContext();

    return (
        <button
            type="button"
            ref={(el) => {
                setTabRef(value, el);
                if (Utils.Type.isFunction(ref)) {
                    ref(el);
                } else if (ref) {
                    ref.current = el;
                }
            }}
            className={cn(
                TabTriggerVariants({ variant, size }),
                "z-20 gap-2 text-muted-foreground data-[state=active]:text-accent-foreground",
                className
            )}
            data-state={activeValue === value ? "active" : "inactive"}
            onClick={() => handleTabClick(value)}
            {...props}
        >
            {children}
        </button>
    );
});
Trigger.displayName = "TabsTrigger";

// Content component for tab panels
export interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
    value: string;
}

const Content = React.forwardRef<HTMLDivElement, TabsContentProps>(({ className, value, children, ...props }, ref) => {
    const { activeValue } = useTabsContext();
    const isActive = value === activeValue;

    if (!isActive) {
        return null;
    }

    const { onDrag, onDragStart, onDragEnd, onAnimationStart, onAnimationEnd, onTransitionEnd, ...divProps } = props;

    return (
        <motion.div
            ref={ref}
            className={cn(
                "ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                className
            )}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            {...divProps}
        >
            {children}
        </motion.div>
    );
});
Content.displayName = "TabsContent";

export default {
    Provider,
    useTabsContext,
    List,
    Trigger,
    Content,
    TabsVariants,
};
