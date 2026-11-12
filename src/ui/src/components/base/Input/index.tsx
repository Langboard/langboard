/* eslint-disable @/max-len */
"use client";

import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn, composeRefs } from "@/core/utils/ComponentUtils";
import { Eye, EyeOff, X } from "lucide-react";
import Box from "@/components/base/Box";
import Form from "@/components/base/Form";

export const InputVariants = cva(
    "flex w-full rounded-md bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:opacity-50 transition-colors duration-150 motion-reduce:transition-none",
    {
        variants: {
            variant: {
                default: "border border-input ring-offset-background focus-visible:ring-2 focus-visible:ring-ring",
                destructive: "border-destructive focus-visible:ring-destructive",
                warning: "border-warning-border focus-visible:ring-2 focus-visible:ring-warning-border",
                ghost: "border-transparent focus-visible:ring-transparent",
            },
            h: {
                default: "h-9 px-3 py-2",
                sm: "h-[28px] px-2 py-1 text-xs",
                lg: "h-10 px-4 py-2",
                xl: "h-12 px-6 py-3 text-base",
            },
        },
        defaultVariants: {
            variant: "default",
            h: "default",
        },
    }
);

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "h">, VariantProps<typeof InputVariants> {
    wrapperProps?: React.ComponentProps<typeof Box>;
    leftIcon?: React.ReactNode;
    rightIcon?: React.ReactNode;
    isFormControl?: bool;
    error?: bool;
    clearable?: bool;
    onClear?: () => void;
    children?: React.ReactNode;
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
    (
        {
            className,
            variant,
            h,
            type = "text",
            wrapperProps,
            leftIcon,
            rightIcon,
            isFormControl,
            error,
            clearable,
            onClear,
            value,
            children,
            ...props
        },
        ref
    ) => {
        const [showPassword, setShowPassword] = React.useState(false);
        const [internalValue, setInternalValue] = React.useState(props.defaultValue || "");
        const inputRef = React.useRef<HTMLInputElement>(null);

        const inputVariant = error ? "destructive" : variant;
        const isPassword = type === "password";
        const actualType = isPassword && showPassword ? "text" : type;

        // Determine if this is a controlled component
        const isControlled = value !== undefined;
        const inputValue = isControlled ? value : internalValue;
        const showClearButton = clearable && inputValue && String(inputValue).length > 0;

        const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
            if (!isControlled) {
                setInternalValue(e.target.value);
            }
            props.onChange?.(e);
        };

        const handleClear = () => {
            const clearEvent = {
                target: { value: "" },
                currentTarget: { value: "" },
            } as React.ChangeEvent<HTMLInputElement>;

            if (!isControlled) {
                setInternalValue("");
            }
            onClear?.();
            props.onChange?.(clearEvent);
        };

        const togglePasswordVisibility = () => {
            setShowPassword(!showPassword);
            inputRef.current?.focus();
        };

        let inputComp = (
            <input
                data-ui-control=""
                type={actualType}
                className={cn(
                    InputVariants({ variant: inputVariant, h, className }),
                    leftIcon && "pl-10",
                    rightIcon || isPassword || showClearButton
                )}
                ref={composeRefs(ref, inputRef)}
                {...(isControlled ? { value: inputValue } : { defaultValue: props.defaultValue })}
                onChange={handleInputChange}
                {...(({ defaultValue, ...rest }) => rest)(props)}
            />
        );

        if (isFormControl) {
            inputComp = <Form.Control asChild>{inputComp}</Form.Control>;
        }

        return (
            <Box position="relative" w="full" {...wrapperProps}>
                {leftIcon && (
                    <div className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-muted-foreground [&_svg]:size-4 [&_svg]:shrink-0">
                        {leftIcon}
                    </div>
                )}

                {inputComp}

                {children}

                {/* Right side icons container */}
                {(rightIcon || isPassword || showClearButton) && (
                    <div className="absolute right-3 top-1/2 z-10 flex -translate-y-1/2 items-center gap-1">
                        {/* Custom right icon */}
                        {rightIcon && <div className="text-muted-foreground [&_svg]:size-4 [&_svg]:shrink-0">{rightIcon}</div>}

                        {/* Clear button */}
                        {showClearButton && (
                            <button
                                type="button"
                                onClick={handleClear}
                                className="text-muted-foreground transition-colors hover:text-foreground [&_svg]:size-4 [&_svg]:shrink-0"
                                tabIndex={-1}
                            >
                                <X />
                            </button>
                        )}

                        {/* Password visibility toggle */}
                        {isPassword && (
                            <button
                                type="button"
                                onClick={togglePasswordVisibility}
                                className="text-muted-foreground transition-colors hover:text-foreground [&_svg]:size-4 [&_svg]:shrink-0"
                                tabIndex={-1}
                            >
                                {showPassword ? <EyeOff /> : <Eye />}
                            </button>
                        )}
                    </div>
                )}
            </Box>
        );
    }
);

Input.displayName = "Input";

export default Input;
