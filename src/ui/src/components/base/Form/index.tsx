/**
 * Original file: https://github.com/radix-ui/primitives/blob/main/packages/react/form/src/Form.tsx
 * GitHub repository: https://github.com/radix-ui/primitives
 * Author: radix-ui
 * Date: 2024/06/10
 * License: MIT
 */

/* eslint-disable @typescript-eslint/no-unsafe-function-type */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { composeEventHandlers } from "@radix-ui/primitive";
import { useComposedRefs } from "@radix-ui/react-compose-refs";
import { createContextScope } from "@radix-ui/react-context";
import type { Scope } from "@radix-ui/react-context";
import { useId } from "@radix-ui/react-id";
import { Label as LabelPrimitive } from "@radix-ui/react-label";
import { Primitive } from "@radix-ui/react-primitive";
import * as React from "react";
import { useTranslation } from "react-i18next";

type ScopedProps<P> = P & { __scopeForm?: Scope };
const [createFormContext, createFormScope] = createContextScope("Form");

/* -------------------------------------------------------------------------------------------------
 * Form
 * -----------------------------------------------------------------------------------------------*/

const FORM_NAME = "Form";

type ValidityMap = { [fieldName: string]: ValidityState | undefined };
type CustomMatcherEntriesMap = { [fieldName: string]: CustomMatcherEntry[] };
type CustomErrorsMap = { [fieldName: string]: Record<string, bool> };

type ValidationContextValue = {
    getFieldValidity(fieldName: string): ValidityState | undefined;
    onFieldValidityChange(fieldName: string, validity: ValidityState): void;

    getFieldCustomMatcherEntries(fieldName: string): CustomMatcherEntry[];
    onFieldCustomMatcherEntryAdd(fieldName: string, matcherEntry: CustomMatcherEntry): void;
    onFieldCustomMatcherEntryRemove(fieldName: string, matcherEntryId: string): void;

    getFieldCustomErrors(fieldName: string): Record<string, bool>;
    onFieldCustomErrorsChange(fieldName: string, errors: Record<string, bool>): void;

    onFieldValiditionClear(fieldName: string): void;
};
const [ValidationProvider, useValidationContext] = createFormContext<ValidationContextValue>(FORM_NAME);

type MessageIdsMap = { [fieldName: string]: Set<string> };

type AriaDescriptionContextValue = {
    onFieldMessageIdAdd(fieldName: string, id: string): void;
    onFieldMessageIdRemove(fieldName: string, id: string): void;
    getFieldDescription(fieldName: string): string | undefined;
};
const [AriaDescriptionProvider, useAriaDescriptionContext] = createFormContext<AriaDescriptionContextValue>(FORM_NAME);

type FormElement = React.ComponentRef<typeof Primitive.form>;
type PrimitiveFormProps = React.ComponentPropsWithoutRef<typeof Primitive.form>;
interface FormProps extends PrimitiveFormProps {
    onClearServerErrors?(): void;
}

const Form = React.forwardRef<FormElement, FormProps>((props: ScopedProps<FormProps>, forwardedRef) => {
    const { __scopeForm, onClearServerErrors = () => {}, ...rootProps } = props;
    const formRef = React.useRef<HTMLFormElement>(null);
    const composedFormRef = useComposedRefs(forwardedRef, formRef);

    // native validity per field
    const [validityMap, setValidityMap] = React.useState<ValidityMap>({});
    const getFieldValidity: ValidationContextValue["getFieldValidity"] = React.useCallback((fieldName) => validityMap[fieldName], [validityMap]);
    const handleFieldValidityChange: ValidationContextValue["onFieldValidityChange"] = React.useCallback(
        (fieldName, validity) =>
            setValidityMap((prevValidityMap) => ({
                ...prevValidityMap,
                [fieldName]: { ...(prevValidityMap[fieldName] ?? {}), ...validity },
            })),
        []
    );
    const handleFieldValiditionClear: ValidationContextValue["onFieldValiditionClear"] = React.useCallback((fieldName) => {
        setValidityMap((prevValidityMap) => ({ ...prevValidityMap, [fieldName]: undefined }));
        setCustomErrorsMap((prevCustomErrorsMap) => ({ ...prevCustomErrorsMap, [fieldName]: {} }));
    }, []);

    // custom matcher entries per field
    const [customMatcherEntriesMap, setCustomMatcherEntriesMap] = React.useState<CustomMatcherEntriesMap>({});
    const getFieldCustomMatcherEntries: ValidationContextValue["getFieldCustomMatcherEntries"] = React.useCallback(
        (fieldName) => customMatcherEntriesMap[fieldName] ?? [],
        [customMatcherEntriesMap]
    );
    const handleFieldCustomMatcherAdd: ValidationContextValue["onFieldCustomMatcherEntryAdd"] = React.useCallback((fieldName, matcherEntry) => {
        setCustomMatcherEntriesMap((prevCustomMatcherEntriesMap) => ({
            ...prevCustomMatcherEntriesMap,
            [fieldName]: [...(prevCustomMatcherEntriesMap[fieldName] ?? []), matcherEntry],
        }));
    }, []);
    const handleFieldCustomMatcherRemove: ValidationContextValue["onFieldCustomMatcherEntryRemove"] = React.useCallback(
        (fieldName, matcherEntryId) => {
            setCustomMatcherEntriesMap((prevCustomMatcherEntriesMap) => ({
                ...prevCustomMatcherEntriesMap,
                [fieldName]: (prevCustomMatcherEntriesMap[fieldName] ?? []).filter((matcherEntry) => matcherEntry.id !== matcherEntryId),
            }));
        },
        []
    );

    // custom errors per field
    const [customErrorsMap, setCustomErrorsMap] = React.useState<CustomErrorsMap>({});
    const getFieldCustomErrors: ValidationContextValue["getFieldCustomErrors"] = React.useCallback(
        (fieldName) => customErrorsMap[fieldName] ?? {},
        [customErrorsMap]
    );
    const handleFieldCustomErrorsChange: ValidationContextValue["onFieldCustomErrorsChange"] = React.useCallback((fieldName, customErrors) => {
        setCustomErrorsMap((prevCustomErrorsMap) => ({
            ...prevCustomErrorsMap,
            [fieldName]: { ...(prevCustomErrorsMap[fieldName] ?? {}), ...customErrors },
        }));
    }, []);

    // messageIds per field
    const [messageIdsMap, setMessageIdsMap] = React.useState<MessageIdsMap>({});
    const handleFieldMessageIdAdd: AriaDescriptionContextValue["onFieldMessageIdAdd"] = React.useCallback((fieldName, id) => {
        setMessageIdsMap((prevMessageIdsMap) => {
            const fieldDescriptionIds = new Set(prevMessageIdsMap[fieldName]).add(id);
            return { ...prevMessageIdsMap, [fieldName]: fieldDescriptionIds };
        });
    }, []);
    const handleFieldMessageIdRemove: AriaDescriptionContextValue["onFieldMessageIdRemove"] = React.useCallback((fieldName, id) => {
        setMessageIdsMap((prevMessageIdsMap) => {
            const fieldDescriptionIds = new Set(prevMessageIdsMap[fieldName]);
            fieldDescriptionIds.delete(id);
            return { ...prevMessageIdsMap, [fieldName]: fieldDescriptionIds };
        });
    }, []);
    const getFieldDescription: AriaDescriptionContextValue["getFieldDescription"] = React.useCallback(
        (fieldName) => Array.from(messageIdsMap[fieldName] ?? []).join(" ") || undefined,
        [messageIdsMap]
    );

    return (
        <ValidationProvider
            scope={__scopeForm}
            getFieldValidity={getFieldValidity}
            onFieldValidityChange={handleFieldValidityChange}
            getFieldCustomMatcherEntries={getFieldCustomMatcherEntries}
            onFieldCustomMatcherEntryAdd={handleFieldCustomMatcherAdd}
            onFieldCustomMatcherEntryRemove={handleFieldCustomMatcherRemove}
            getFieldCustomErrors={getFieldCustomErrors}
            onFieldCustomErrorsChange={handleFieldCustomErrorsChange}
            onFieldValiditionClear={handleFieldValiditionClear}
        >
            <AriaDescriptionProvider
                scope={__scopeForm}
                onFieldMessageIdAdd={handleFieldMessageIdAdd}
                onFieldMessageIdRemove={handleFieldMessageIdRemove}
                getFieldDescription={getFieldDescription}
            >
                <Primitive.form
                    {...rootProps}
                    ref={composedFormRef}
                    // focus first invalid control when the form is submitted
                    onInvalid={composeEventHandlers(props.onInvalid, (event) => {
                        const firstInvalidControl = getFirstInvalidControl(event.currentTarget);
                        if (firstInvalidControl === event.target) firstInvalidControl.focus();

                        // prevent default browser UI for form validation
                        event.preventDefault();
                    })}
                    // clear server errors when the form is re-submitted
                    onSubmit={composeEventHandlers(props.onSubmit, onClearServerErrors, {
                        checkForDefaultPrevented: false,
                    })}
                    // clear server errors when the form is reset
                    onReset={composeEventHandlers(props.onReset, onClearServerErrors)}
                />
            </AriaDescriptionProvider>
        </ValidationProvider>
    );
});

Form.displayName = FORM_NAME;

/* -------------------------------------------------------------------------------------------------
 * FormField
 * -----------------------------------------------------------------------------------------------*/

const FIELD_NAME = "FormField";

type FormFieldContextValue = {
    id: string;
    name: string;
    serverInvalid: bool;
};
const [FormFieldProvider, useFormFieldContext] = createFormContext<FormFieldContextValue>(FIELD_NAME);

type FormFieldElement = React.ComponentRef<typeof Primitive.div>;
type PrimitiveDivProps = React.ComponentPropsWithoutRef<typeof Primitive.div>;
interface FormFieldProps extends PrimitiveDivProps {
    name: string;
    serverInvalid?: bool;
}

const FormField = React.forwardRef<FormFieldElement, FormFieldProps>((props: ScopedProps<FormFieldProps>, forwardedRef) => {
    const { __scopeForm, name, serverInvalid = false, ...fieldProps } = props;
    const validationContext = useValidationContext(FIELD_NAME, __scopeForm);
    const validity = validationContext.getFieldValidity(name);
    const id = useId();

    return (
        <FormFieldProvider scope={__scopeForm} id={id} name={name} serverInvalid={serverInvalid}>
            <Primitive.div
                data-valid={getValidAttribute(validity, serverInvalid)}
                data-invalid={getInvalidAttribute(validity, serverInvalid)}
                {...fieldProps}
                ref={forwardedRef}
            />
        </FormFieldProvider>
    );
});

FormField.displayName = FIELD_NAME;

/* -------------------------------------------------------------------------------------------------
 * FormLabel
 * -----------------------------------------------------------------------------------------------*/

const LABEL_NAME = "FormLabel";

type FormLabelElement = React.ComponentRef<typeof LabelPrimitive>;
type LabelProps = React.ComponentPropsWithoutRef<typeof LabelPrimitive>;
interface FormLabelProps extends LabelProps {}

const FormLabel = React.forwardRef<FormLabelElement, FormLabelProps>((props: ScopedProps<FormLabelProps>, forwardedRef) => {
    const { __scopeForm, ...labelProps } = props;
    const validationContext = useValidationContext(LABEL_NAME, __scopeForm);
    const fieldContext = useFormFieldContext(LABEL_NAME, __scopeForm);
    const htmlFor = (labelProps as any).htmlFor || fieldContext.id;
    const validity = validationContext.getFieldValidity(fieldContext.name);

    return (
        <LabelPrimitive
            data-valid={getValidAttribute(validity, fieldContext.serverInvalid)}
            data-invalid={getInvalidAttribute(validity, fieldContext.serverInvalid)}
            {...labelProps}
            ref={forwardedRef}
            htmlFor={htmlFor}
        />
    );
});

FormLabel.displayName = LABEL_NAME;

/* -------------------------------------------------------------------------------------------------
 * FormControl
 * -----------------------------------------------------------------------------------------------*/

const CONTROL_NAME = "FormControl";

type FormControlElement = React.ComponentRef<typeof Primitive.input>;
type PrimitiveInputProps = React.ComponentPropsWithoutRef<typeof Primitive.input>;
interface FormControlProps extends PrimitiveInputProps {}

const FormControl = React.forwardRef<FormControlElement, FormControlProps>((props: ScopedProps<FormControlProps>, forwardedRef) => {
    const { __scopeForm, ...controlProps } = props;

    const validationContext = useValidationContext(CONTROL_NAME, __scopeForm);
    const fieldContext = useFormFieldContext(CONTROL_NAME, __scopeForm);
    const ariaDescriptionContext = useAriaDescriptionContext(CONTROL_NAME, __scopeForm);

    const ref = React.useRef<FormControlElement>(null);
    const composedRef = useComposedRefs(forwardedRef, ref);
    const name = controlProps.name || fieldContext.name;
    const id = controlProps.id || fieldContext.id;
    const customMatcherEntries = validationContext.getFieldCustomMatcherEntries(name);

    const { onFieldValidityChange, onFieldCustomErrorsChange, onFieldValiditionClear } = validationContext;
    const updateControlValidity = React.useCallback(
        async (control: FormControlElement) => {
            //------------------------------------------------------------------------------------------
            // 1. first, if we have built-in errors we stop here

            if (hasBuiltInError(control.validity)) {
                const controlValidity = validityStateToObject(control.validity);
                onFieldValidityChange(name, controlValidity);
                return;
            }

            //------------------------------------------------------------------------------------------
            // 2. then gather the form data to give to custom matchers for cross-comparisons

            const formData = control.form ? new FormData(control.form) : new FormData();
            const matcherArgs: CustomMatcherArgs = [control.value, formData];

            //------------------------------------------------------------------------------------------
            // 3. split sync and async custom matcher entries

            const syncCustomMatcherEntries: Array<SyncCustomMatcherEntry> = [];
            const ayncCustomMatcherEntries: Array<AsyncCustomMatcherEntry> = [];
            customMatcherEntries.forEach((customMatcherEntry) => {
                if (isAsyncCustomMatcherEntry(customMatcherEntry, matcherArgs)) {
                    ayncCustomMatcherEntries.push(customMatcherEntry);
                } else if (isSyncCustomMatcherEntry(customMatcherEntry)) {
                    syncCustomMatcherEntries.push(customMatcherEntry);
                }
            });

            //------------------------------------------------------------------------------------------
            // 4. run sync custom matchers and update control validity / internal validity + errors

            const syncCustomErrors = syncCustomMatcherEntries.map(({ id, match }) => {
                return [id, match(...matcherArgs)] as const;
            });
            const syncCustomErrorsById = Object.fromEntries(syncCustomErrors);
            const hasSyncCustomErrors = Object.values(syncCustomErrorsById).some(Boolean);
            const hasCustomError = hasSyncCustomErrors;
            control.setCustomValidity(hasCustomError ? DEFAULT_INVALID_MESSAGE : "");
            const controlValidity = validityStateToObject(control.validity);
            onFieldValidityChange(name, controlValidity);
            onFieldCustomErrorsChange(name, syncCustomErrorsById);

            //------------------------------------------------------------------------------------------
            // 5. run async custom matchers and update control validity / internal validity + errors

            if (!hasSyncCustomErrors && ayncCustomMatcherEntries.length > 0) {
                const promisedCustomErrors = ayncCustomMatcherEntries.map(({ id, match }) =>
                    match(...matcherArgs).then((matches) => [id, matches] as const)
                );
                const asyncCustomErrors = await Promise.all(promisedCustomErrors);
                const asyncCustomErrorsById = Object.fromEntries(asyncCustomErrors);
                const hasAsyncCustomErrors = Object.values(asyncCustomErrorsById).some(Boolean);
                const hasCustomError = hasAsyncCustomErrors;
                control.setCustomValidity(hasCustomError ? DEFAULT_INVALID_MESSAGE : "");
                const controlValidity = validityStateToObject(control.validity);
                onFieldValidityChange(name, controlValidity);
                onFieldCustomErrorsChange(name, asyncCustomErrorsById);
            }
        },
        [customMatcherEntries, name, onFieldCustomErrorsChange, onFieldValidityChange]
    );

    React.useEffect(() => {
        const control = ref.current;
        if (control) {
            // We only want validate on change (native `change` event, not React's `onChange`). This is primarily
            // a UX decision, we don't want to validate on every keystroke and React's `onChange` is the `input` event.
            const handleChange = () => updateControlValidity(control);
            control.addEventListener("change", handleChange);
            return () => control.removeEventListener("change", handleChange);
        }
    }, [updateControlValidity]);

    const resetControlValidity = React.useCallback(() => {
        const control = ref.current;
        if (control) {
            control.setCustomValidity("");
            onFieldValiditionClear(name);
        }
    }, [name, onFieldValiditionClear]);

    // reset validity and errors when the form is reset
    React.useEffect(() => {
        const form = ref.current?.form;
        if (form) {
            form.addEventListener("reset", resetControlValidity);
            return () => form.removeEventListener("reset", resetControlValidity);
        }
    }, [resetControlValidity]);

    // focus first invalid control when fields are set as invalid by server
    React.useEffect(() => {
        const control = ref.current;
        const form = control?.closest("form");
        if (form && fieldContext.serverInvalid) {
            const firstInvalidControl = getFirstInvalidControl(form);
            if (firstInvalidControl === control) firstInvalidControl.focus();
        }
    }, [fieldContext.serverInvalid]);

    const validity = validationContext.getFieldValidity(name);

    return (
        <Primitive.input
            data-valid={getValidAttribute(validity, fieldContext.serverInvalid)}
            data-invalid={getInvalidAttribute(validity, fieldContext.serverInvalid)}
            aria-invalid={fieldContext.serverInvalid ? true : undefined}
            aria-describedby={ariaDescriptionContext.getFieldDescription(name)}
            // disable default browser behaviour of showing built-in error message on hover
            title=""
            {...controlProps}
            ref={composedRef}
            id={id}
            name={name}
            onInvalid={composeEventHandlers(props.onInvalid, (event) => {
                const control = event.currentTarget;
                updateControlValidity(control);
            })}
            onChange={composeEventHandlers(props.onChange, () => {
                // reset validity when user changes value
                resetControlValidity();
            })}
        />
    );
});

FormControl.displayName = CONTROL_NAME;

/* -------------------------------------------------------------------------------------------------
 * FormMessage
 * -----------------------------------------------------------------------------------------------*/

const validityMatchers = [
    "badInput",
    "patternMismatch",
    "rangeOverflow",
    "rangeUnderflow",
    "stepMismatch",
    "tooLong",
    "tooShort",
    "typeMismatch",
    "valid",
    "valueMissing",
] as const;
type ValidityMatcher = (typeof validityMatchers)[number];

const DEFAULT_INVALID_MESSAGE = "errors.form.invalid";
const DEFAULT_BUILT_IN_MESSAGES: Record<ValidityMatcher, string | undefined> = {
    badInput: DEFAULT_INVALID_MESSAGE,
    patternMismatch: "errors.form.patternMismatch",
    rangeOverflow: "errors.form.rangeOverflow",
    rangeUnderflow: "errors.form.rangeUnderflow",
    stepMismatch: "errors.form.stepMismatch",
    tooLong: "errors.form.tooLong",
    tooShort: "errors.form.tooShort",
    typeMismatch: "errors.form.typeMismatch",
    valid: undefined,
    valueMissing: "errors.form.valueMissing",
};

const MESSAGE_NAME = "FormMessage";

type FormMessageElement = FormMessageImplElement;
interface FormMessageProps extends Omit<FormMessageImplProps, "name"> {
    match?: ValidityMatcher | CustomMatcher;
    forceMatch?: bool;
    name?: string;
}

const FormMessage = React.forwardRef<FormMessageElement, FormMessageProps>((props: ScopedProps<FormMessageProps>, forwardedRef) => {
    const [t] = useTranslation();
    const { match, name: nameProp, ...messageProps } = props;
    const fieldContext = useFormFieldContext(MESSAGE_NAME, props.__scopeForm);
    const name = nameProp ?? fieldContext.name;

    if (match === undefined) {
        return (
            <FormMessageImpl {...messageProps} ref={forwardedRef} name={name}>
                {props.children || t(DEFAULT_INVALID_MESSAGE)}
            </FormMessageImpl>
        );
    } else if (typeof match === "function") {
        return <FormCustomMessage match={match} {...messageProps} ref={forwardedRef} name={name} />;
    } else {
        return <FormBuiltInMessage match={match} {...messageProps} ref={forwardedRef} name={name} />;
    }
});

FormMessage.displayName = MESSAGE_NAME;

type FormBuiltInMessageElement = FormMessageImplElement;
interface FormBuiltInMessageProps extends FormMessageImplProps {
    match: ValidityMatcher;
    forceMatch?: bool;
    name: string;
}

const FormBuiltInMessage = React.forwardRef<FormBuiltInMessageElement, FormBuiltInMessageProps>(
    (props: ScopedProps<FormBuiltInMessageProps>, forwardedRef) => {
        const [t] = useTranslation();
        const { match, forceMatch = false, name, children, ...messageProps } = props;
        const validationContext = useValidationContext(MESSAGE_NAME, messageProps.__scopeForm);
        const validity = validationContext.getFieldValidity(name);
        const matches = forceMatch || validity?.[match];

        if (matches) {
            return (
                <FormMessageImpl ref={forwardedRef} {...messageProps} name={name}>
                    {children ?? (DEFAULT_BUILT_IN_MESSAGES[match] ? t(DEFAULT_BUILT_IN_MESSAGES[match]) : undefined)}
                </FormMessageImpl>
            );
        }

        return null;
    }
);

type FormCustomMessageElement = React.ComponentRef<typeof FormMessageImpl>;
interface FormCustomMessageProps extends React.ComponentPropsWithoutRef<typeof FormMessageImpl> {
    match: CustomMatcher;
    forceMatch?: bool;
    name: string;
}

const FormCustomMessage = React.forwardRef<FormCustomMessageElement, FormCustomMessageProps>(
    (props: ScopedProps<FormCustomMessageProps>, forwardedRef) => {
        const { match, forceMatch = false, name, id: idProp, children, ...messageProps } = props;
        const validationContext = useValidationContext(MESSAGE_NAME, messageProps.__scopeForm);
        const ref = React.useRef<FormCustomMessageElement>(null);
        const composedRef = useComposedRefs(forwardedRef, ref);
        const _id = useId();
        const id = idProp ?? _id;

        const customMatcherEntry = React.useMemo(() => ({ id, match }), [id, match]);
        const { onFieldCustomMatcherEntryAdd, onFieldCustomMatcherEntryRemove } = validationContext;
        React.useEffect(() => {
            onFieldCustomMatcherEntryAdd(name, customMatcherEntry);
            return () => onFieldCustomMatcherEntryRemove(name, customMatcherEntry.id);
        }, [customMatcherEntry, name, onFieldCustomMatcherEntryAdd, onFieldCustomMatcherEntryRemove]);

        const validity = validationContext.getFieldValidity(name);
        const customErrors = validationContext.getFieldCustomErrors(name);
        const hasMatchingCustomError = customErrors[id];
        const matches = forceMatch || (validity && !hasBuiltInError(validity) && hasMatchingCustomError);

        if (matches) {
            return (
                <FormMessageImpl id={id} ref={composedRef} {...messageProps} name={name}>
                    {children ?? DEFAULT_INVALID_MESSAGE}
                </FormMessageImpl>
            );
        }

        return null;
    }
);

type FormMessageImplElement = React.ComponentRef<typeof Primitive.span>;
type PrimitiveSpanProps = React.ComponentPropsWithoutRef<typeof Primitive.span>;
interface FormMessageImplProps extends PrimitiveSpanProps {
    name: string;
}

const FormMessageImpl = React.forwardRef<FormMessageImplElement, FormMessageImplProps>((props: ScopedProps<FormMessageImplProps>, forwardedRef) => {
    const { __scopeForm, id: idProp, name, ...messageProps } = props;
    const ariaDescriptionContext = useAriaDescriptionContext(MESSAGE_NAME, __scopeForm);
    const _id = useId();
    const id = idProp ?? _id;

    const { onFieldMessageIdAdd, onFieldMessageIdRemove } = ariaDescriptionContext;
    React.useEffect(() => {
        onFieldMessageIdAdd(name, id);
        return () => onFieldMessageIdRemove(name, id);
    }, [name, id, onFieldMessageIdAdd, onFieldMessageIdRemove]);

    return <Primitive.span id={id} {...messageProps} ref={forwardedRef} />;
});

/* -------------------------------------------------------------------------------------------------
 * FormValidityState
 * -----------------------------------------------------------------------------------------------*/

const VALIDITY_STATE_NAME = "FormValidityState";

interface FormValidityStateProps {
    children(validity: ValidityState | undefined): React.ReactNode;
    name?: string;
}

const FormValidityState = (props: ScopedProps<FormValidityStateProps>) => {
    const { __scopeForm, name: nameProp, children } = props;
    const validationContext = useValidationContext(VALIDITY_STATE_NAME, __scopeForm);
    const fieldContext = useFormFieldContext(VALIDITY_STATE_NAME, __scopeForm);
    const name = nameProp ?? fieldContext.name;
    const validity = validationContext.getFieldValidity(name);
    return <>{children(validity)}</>;
};

FormValidityState.displayName = VALIDITY_STATE_NAME;

/* -------------------------------------------------------------------------------------------------
 * FormSubmit
 * -----------------------------------------------------------------------------------------------*/

const SUBMIT_NAME = "FormSubmit";

type FormSubmitElement = React.ComponentRef<typeof Primitive.button>;
type PrimitiveButtonProps = React.ComponentPropsWithoutRef<typeof Primitive.button>;
interface FormSubmitProps extends PrimitiveButtonProps {}

const FormSubmit = React.forwardRef<FormSubmitElement, FormSubmitProps>((props: ScopedProps<FormSubmitProps>, forwardedRef) => {
    const { __scopeForm, ...submitProps } = props;
    return <Primitive.button type="submit" {...submitProps} ref={forwardedRef} />;
});

FormSubmit.displayName = SUBMIT_NAME;

/* -----------------------------------------------------------------------------------------------*/

type ValidityStateKey = keyof ValidityState;
type SyncCustomMatcher = (value: string, formData: FormData) => bool;
type AsyncCustomMatcher = (value: string, formData: FormData) => Promise<bool>;
type CustomMatcher = SyncCustomMatcher | AsyncCustomMatcher;
type CustomMatcherEntry = { id: string; match: CustomMatcher };
type SyncCustomMatcherEntry = { id: string; match: SyncCustomMatcher };
type AsyncCustomMatcherEntry = { id: string; match: AsyncCustomMatcher };
type CustomMatcherArgs = [string, FormData];

function validityStateToObject(validity: ValidityState) {
    const object: any = {};
    for (const key in validity) {
        object[key] = validity[key as ValidityStateKey];
    }
    return object as Record<ValidityStateKey, bool>;
}

function isHTMLElement(element: any): element is HTMLElement {
    return element instanceof HTMLElement;
}

function isFormControl(element: any): element is { validity: ValidityState } {
    return "validity" in element;
}

function isInvalid(control: HTMLElement) {
    return isFormControl(control) && (control.validity.valid === false || control.getAttribute("aria-invalid") === "true");
}

function getFirstInvalidControl(form: HTMLFormElement): HTMLElement | undefined {
    const elements = form.elements;
    const [firstInvalidControl] = Array.from(elements).filter(isHTMLElement).filter(isInvalid);
    return firstInvalidControl;
}

function isAsyncCustomMatcherEntry(entry: CustomMatcherEntry, args: CustomMatcherArgs): entry is AsyncCustomMatcherEntry {
    return entry.match.constructor.name === "AsyncFunction" || returnsPromise(entry.match, args);
}

function isSyncCustomMatcherEntry(entry: CustomMatcherEntry): entry is SyncCustomMatcherEntry {
    return entry.match.constructor.name === "Function";
}

function returnsPromise(func: Function, args: Array<unknown>) {
    return func(...args) instanceof Promise;
}

function hasBuiltInError(validity: ValidityState) {
    let error = false;
    for (const validityKey in validity) {
        const key = validityKey as ValidityStateKey;
        if (key !== "valid" && key !== "customError" && validity[key]) {
            error = true;
            break;
        }
    }
    return error;
}

function getValidAttribute(validity: ValidityState | undefined, serverInvalid: bool) {
    if (validity?.valid === true && !serverInvalid) return true;
    return undefined;
}
function getInvalidAttribute(validity: ValidityState | undefined, serverInvalid: bool) {
    if (validity?.valid === false || serverInvalid) return true;
    return undefined;
}

/* -----------------------------------------------------------------------------------------------*/

const Root = Form;
const Field = FormField;
const Label = FormLabel;
const Control = FormControl;
const Message = FormMessage;
const ValidityState = FormValidityState;
const Submit = FormSubmit;

export default {
    Control,
    createFormScope,
    Field,
    Label,
    Message,
    Root,
    Submit,
    ValidityState,
};
export type { FormControlProps, FormFieldProps, FormLabelProps, FormMessageProps, FormProps, FormSubmitProps, FormValidityStateProps };
