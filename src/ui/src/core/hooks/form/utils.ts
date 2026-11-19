import { exists } from "i18next";
import { TResponseErrors } from "@/core/helpers/setupApiErrorHandler";
import { IBaseValidationSchema } from "@/core/hooks/form/types";

export const convertValidationName = (key: string) => {
    switch (key) {
        case "required":
        case "missing":
            return "missing";
        case "min":
            return "min";
        case "max":
            return "max";
        case "range":
            return "range";
        case "pattern":
        case "email":
        case "sameWith":
        case "mimeType":
        case "value_error":
        case "enum":
        default:
            if (key.startsWith("custom:")) {
                return key.replace("custom:", "");
            }
            return "invalid";
    }
};

export const convertValidationToLangKey = (prefix: string, key: keyof IBaseValidationSchema | (string & {}), inputName: string) => {
    const validation = convertValidationName(key);
    const fieldKey = `${prefix}.${validation}.${inputName}`;
    if (exists(fieldKey)) return fieldKey;
    return validation === "missing" ? "errors.form.valueMissing" : "errors.form.invalid";
};

export const handleResponseErrors = (prefix: string, errors: TResponseErrors) => {
    if (!errors) {
        return undefined;
    }

    const newErrors: Record<string, string> = {};
    const errorTypes = Object.keys(errors);

    for (let i = 0; i < errorTypes.length; ++i) {
        const errorType = errorTypes[i];
        const errorFields = errors[errorType]?.body;
        if (!Array.isArray(errorFields)) {
            continue;
        }

        for (let j = 0; j < errorFields.length; ++j) {
            const fieldName = errorFields[j];
            if (typeof fieldName !== "string" || !fieldName) continue;
            newErrors[fieldName] = convertValidationToLangKey(prefix, errorType, fieldName);
        }
    }

    return newErrors;
};
