import type { TriggerComboboxPluginOptions } from "@platejs/combobox";
import { IS_ANDROID } from "platejs";
import { createPlatePlugin } from "platejs/react";

export const NativeComboboxPlugin = createPlatePlugin({
    key: "nativeCombobox",
    handlers: {
        onDOMBeforeInput: ({ editor, event }) => {
            const input = ("nativeEvent" in event ? event.nativeEvent : event) as InputEvent;
            if (!IS_ANDROID || !input.cancelable || input.isComposing || editor.api.isComposing() || input.inputType !== "insertText" || !input.data)
                return;

            // Android's native diff manager restores the DOM selection after
            // inserting a void input, blurring and cancelling the combobox.
            // Cancel only supported trigger events and use the existing transform.
            const isTrigger = editor.meta.pluginList.some((plugin) => {
                const { trigger, createComboboxInput } = editor.getOptions(plugin) as TriggerComboboxPluginOptions;
                if (!createComboboxInput) return false;
                if (trigger instanceof RegExp) return trigger.test(input.data!);
                return Array.isArray(trigger) ? trigger.includes(input.data!) : trigger === input.data;
            });
            if (!isTrigger) return;

            const target = input.getTargetRanges()[0];
            const at = target && editor.api.toSlateRange(target, { exactMatch: false, suppressThrow: true });
            if (at) editor.tf.select(at);
            event.preventDefault();
            editor.tf.insertText(input.data);
            return true;
        },
    },
});
