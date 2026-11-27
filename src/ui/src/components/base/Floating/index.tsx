import * as React from "react";
import * as Button from "@/components/base/Floating/Button";
import Nav from "@/components/base/Floating/Nav";
import Form from "@/components/base/Form";
import BaseInput, { InputProps } from "@/components/base/Input";
import BaseLabel from "@/components/base/Label";
import BaseTextarea, { type TextareaProps as BaseTextareaProps } from "@/components/base/Textarea";
import { cn } from "@/core/utils/ComponentUtils";
import { Utils } from "@langboard/core/utils";
import Select from "@/components/base/Select";

export interface TextareaProps extends BaseTextareaProps {}

const Input = React.forwardRef<HTMLInputElement, InputProps>(({ className, ...props }, ref) => {
    return <BaseInput placeholder=" " className={cn("peer", className)} ref={ref} {...props} />;
});
Input.displayName = "FloatingInput";

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(({ className, ...props }, ref) => {
    return <BaseTextarea placeholder=" " className={cn("peer", className)} ref={ref} {...props} />;
});
Textarea.displayName = "FloatingTextarea";

interface ILabelProps {
    required?: bool;
    isTextarea?: bool;
}

const Label = React.forwardRef<React.ComponentRef<typeof BaseLabel>, React.ComponentPropsWithoutRef<typeof BaseLabel> & ILabelProps>(
    ({ className, required, isTextarea, ...props }, ref) => {
        const classNames = cn(
            "peer-focus:secondary peer-focus:dark:secondary",
            "absolute start-2 top-1.5 z-10 origin-[0] -translate-y-4 scale-75 transform cursor-text",
            "bg-background px-2 text-sm text-gray-500 duration-300 dark:bg-background",
            isTextarea ? "peer-placeholder-shown:top-7" : "peer-placeholder-shown:top-1/2 peer-placeholder-shown:-translate-y-1/2",
            "peer-placeholder-shown:scale-100",
            "peer-focus:top-1.5 peer-focus:-translate-y-4 peer-focus:scale-75 peer-focus:px-2",
            "rtl:peer-focus:left-auto rtl:peer-focus:translate-x-1/4",
            required && "after:content-['*'] after:ml-1 after:text-red-500",
            className
        );

        return <BaseLabel className={classNames} ref={ref} {...props} />;
    }
);
Label.displayName = "FloatingLabel";

type LabelInputProps = InputProps & { label: React.ReactNode; isFormControl?: bool };

const LabelInput = React.forwardRef<React.ComponentRef<typeof Input>, LabelInputProps>(({ label, id, ...props }, ref) => {
    id = id ?? `floating-input-${Utils.String.Token.shortUUID()}`;

    return (
        <Input id={id} ref={ref} {...props}>
            <Label className="select-none" htmlFor={id} required={props.required}>
                {label}
            </Label>
        </Input>
    );
});
LabelInput.displayName = "FloatingLabelInput";

type LabelTextareaProps = BaseTextareaProps & { wrapperClassNames?: string; label: React.ReactNode; isFormControl?: bool };

const LabelTextarea = React.forwardRef<React.ComponentRef<typeof Textarea>, React.PropsWithoutRef<LabelTextareaProps>>(
    ({ wrapperClassNames, label, isFormControl, id, ...props }, ref) => {
        id = id ?? `floating-textarea-${Utils.String.Token.shortUUID()}`;

        let comp = <Textarea id={id} ref={ref} {...props} />;
        if (isFormControl) {
            comp = <Form.Control asChild>{comp}</Form.Control>;
        }

        return (
            <div className={cn("relative", wrapperClassNames)}>
                {comp}
                <Label className="select-none" htmlFor={id} isTextarea required={props.required}>
                    {label}
                </Label>
            </div>
        );
    }
);
LabelTextarea.displayName = "FloatingLabelTextarea";

export type TLabelSelectProps = React.ComponentProps<(typeof Select)["Root"]> & {
    id?: string;
    options: React.ReactNode | React.ReactNode[];
    className?: string;
    contentClassName?: string;
    label: React.ReactNode;
    ref?: React.Ref<HTMLButtonElement>;
    triggerStyle?: React.CSSProperties;
};

const LabelSelect = ({ label, id, options, className, contentClassName, ref, triggerStyle, ...props }: TLabelSelectProps) => {
    const [shouldShow, setShouldShow] = React.useState(!!props.value || !!props.defaultValue);
    const onValueChange = React.useCallback(
        (value: string) => {
            props.onValueChange?.(value);
            setShouldShow(!!value);
        },
        [shouldShow, setShouldShow, props.onValueChange]
    );
    id = id ?? `floating-select-${Utils.String.Token.shortUUID()}`;

    return (
        <Select.Root {...props} onValueChange={onValueChange}>
            <Select.Trigger className={cn("relative", className)} ref={ref} style={triggerStyle}>
                <Select.Value id={id} className="peer" placeholder={shouldShow ? "" : label} />
                {shouldShow && (
                    <Label className="select-none" htmlFor={id} required={props.required}>
                        {label}
                    </Label>
                )}
            </Select.Trigger>
            <Select.Content className={contentClassName}>{options}</Select.Content>
        </Select.Root>
    );
};
LabelSelect.displayName = "FloatingLabelSelect";

export default {
    Button,
    Input,
    Label,
    LabelInput,
    LabelTextarea,
    LabelSelect,
    Nav,
    Textarea,
};
