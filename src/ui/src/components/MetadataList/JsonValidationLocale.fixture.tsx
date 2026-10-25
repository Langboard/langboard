import "@/i18n";
import i18n from "@/i18n";
import { createRoot } from "react-dom/client";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import BotValueJsonInput from "@/components/bots/BotValueInput/Json";
import MetadataRowJsonViewer from "./MetadataRowJsonViewer";
import { AuthUser } from "@/core/models";
import { EBotPlatform, EBotPlatformRunningType } from "@langboard/core/ai";
import { EEditorCollaborationType } from "@langboard/core/constants";
import Textarea from "@/components/base/Textarea";
import type { ICollaborativeTextareaProps } from "@/components/Collaborative/Textarea";

// The browser test replaces collaboration transport with this local control.
// Production validation, React state, localization, and dialog rendering remain real.
export const CollaborativeFixture = {
    Textarea: ({ onValueChange, collaborationType: _type, uid: _uid, section: _section, field: _field, ...props }: ICollaborativeTextareaProps) => (
        <Textarea
            {...props}
            onChange={(event) => {
                onValueChange?.(event.target.value);
                props.onChange?.(event);
            }}
        />
    ),
};

function Fixture() {
    const [t] = useTranslation();
    const newValueRef = useRef("{}");
    const currentUser = AuthUser.Model.fromOne({ uid: "json-fixture", created_at: new Date(), updated_at: new Date() });
    const valueInputRef = useRef<HTMLInputElement>(null);
    return (
        <main>
            <select aria-label="Locale" defaultValue="en-US" onChange={(event) => void i18n.changeLanguage(event.target.value)}>
                {["en-US", "ko-KR", "ja-JP", "zh-CN"].map((locale) => (
                    <option key={locale} value={locale}>
                        {locale}
                    </option>
                ))}
            </select>
            <output data-testid="expected-error">{t("metadata.errors.Invalid JSON format.")}</output>
            <section data-testid="bot">
                <BotValueJsonInput
                    currentUser={currentUser}
                    label="JSON"
                    platform={EBotPlatform.Default}
                    platformRunningType={EBotPlatformRunningType.Default}
                    value="{}"
                    newValueRef={newValueRef}
                    previewByDialog={false}
                    isValidating={false}
                    disabled={false}
                />
            </section>
            <section data-testid="metadata">
                <MetadataRowJsonViewer
                    collaborationType={EEditorCollaborationType.Card}
                    uid="local-fixture"
                    section="metadata"
                    valueInputRef={valueInputRef}
                    handleValueInput={() => {}}
                    currentValue="{}"
                    canEdit={() => true}
                />
            </section>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
