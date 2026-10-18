import { createRoot } from "react-dom/client";
import { useRef, useState } from "react";
import AvatarUploader from ".";
import Form from "@/components/base/Form";
import useForm from "@/core/hooks/form/useForm";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

const initial = `data:image/svg+xml,${encodeURIComponent(`
    <svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">
        <rect width="96" height="96" fill="navy"/>
    </svg>`)}`;

function Fixture() {
    const dataTransferRef = useRef(new DataTransfer());
    const avatarUrlRef = useRef<string | null>(initial);
    const isDeletedRef = useRef(false);
    const [disabled, setDisabled] = useState(false);
    const [validating, setValidating] = useState(false);
    const [version, setVersion] = useState(0);
    const [url, setUrl] = useState(initial);
    const [submitted, setSubmitted] = useState("");
    const [calls, setCalls] = useState<string[]>([]);
    const [submitting, setSubmitting] = useState(false);
    const { handleSubmit, formRef } = useForm({
        errorLangPrefix: "myAccount.errors",
        isValidatingState: [submitting, setSubmitting],
        schema: { avatar: { mimeType: "image/*" }, delete_avatar: {} },
        inputRefs: { avatar: dataTransferRef, delete_avatar: isDeletedRef },
        successCallback: (data: { avatar?: File[]; delete_avatar?: string }) => {
            setSubmitting(false);
            setSubmitted(
                JSON.stringify({
                    names: Array.from(data.avatar ?? []).map((file) => file.name),
                    deleted: data.delete_avatar ?? "false",
                    preview: avatarUrlRef.current,
                })
            );
        },
    });
    return (
        <main style={{ maxWidth: 360, margin: "32px auto", padding: 16, overflowWrap: "anywhere" }}>
            <h1>Avatar lifecycle</h1>
            <button onClick={() => setDisabled(!disabled)}>Toggle disabled</button>
            <button onClick={() => setValidating(!validating)}>Toggle validating</button>
            <button onClick={() => setVersion(version + 1)}>Update callbacks</button>
            <button onClick={() => setUrl(initial + "#saved")}>Update saved avatar</button>
            <Form.Root ref={formRef} onSubmit={handleSubmit}>
                <AvatarUploader
                    userInitials="QA"
                    initialAvatarUrl={url}
                    dataTransferRef={dataTransferRef}
                    avatarUrlRef={avatarUrlRef}
                    isDeletedRef={isDeletedRef}
                    canRevertUrl
                    disabled={disabled}
                    isValidating={validating}
                    onChange={(files) => setCalls((previous) => [...previous, `change:${version}:${files[0].name}`])}
                    onDeleted={() => setCalls((previous) => [...previous, `delete:${version}`])}
                />
                <button type="submit">Submit avatar</button>
            </Form.Root>
            <output data-testid="submitted">{submitted}</output>
            <output data-testid="calls">{JSON.stringify(calls)}</output>
        </main>
    );
}
void i18n.changeLanguage("en-US").then(() => createRoot(document.getElementById("root")!).render(<Fixture />));
