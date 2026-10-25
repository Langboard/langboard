import { useRef } from "react";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import Dialog from "@/components/base/Dialog";
import useCloudUpload, { CloudFolder } from "@/controllers/api/board/cloud/useCloudUpload";

export default function CloudUpload({ projectUID, folder, enabled }: { projectUID: string; folder: CloudFolder; enabled: boolean }) {
    const [t] = useTranslation();
    const input = useRef<HTMLInputElement>(null);
    const upload = useCloudUpload(projectUID);
    return (
        <div className="space-y-2 px-3 py-2">
            {enabled && (
                <Button size="sm" disabled={upload.busy} onClick={() => input.current?.click()}>
                    {t("cloud.Upload")}
                </Button>
            )}
            <input
                ref={input}
                type="file"
                multiple
                hidden
                aria-label={t("cloud.Upload files")}
                disabled={!enabled || upload.busy}
                onChange={(event) => {
                    upload.start(Array.from(event.target.files ?? []), folder);
                    event.target.value = "";
                }}
            />
            {upload.pending && <p role="status">{t("cloud.Uploading", { name: upload.filename })}</p>}
            {upload.error && (
                <div role="alert" className="space-y-2">
                    <p>{t("cloud.Upload failed", { name: upload.filename })}</p>
                    <Button size="sm" onClick={() => void upload.retry()}>
                        {t("cloud.Retry upload")}
                    </Button>
                    <Button size="sm" variant="secondary" onClick={upload.cancelQueue}>
                        {t("common.Cancel")}
                    </Button>
                </div>
            )}
            <Dialog.Root open={!!upload.prompt} onOpenChange={(open) => !open && upload.decide("cancel")}>
                {upload.prompt && (
                    <Dialog.Content>
                        <Dialog.Header>
                            <Dialog.Title>{t("card.Duplicate attachment")}</Dialog.Title>
                            <Dialog.Description>{t("card.Choose how to upload this file", { name: upload.filename })}</Dialog.Description>
                        </Dialog.Header>
                        <ul className="my-4 max-h-64 space-y-3 overflow-y-auto">
                            {upload.prompt.conflicts.map((conflict) => {
                                const targetName =
                                    upload.prompt!.conflicts.filter((item) => item.name === conflict.name).length > 1
                                        ? `${conflict.name} (${conflict.uid})`
                                        : conflict.name;
                                return (
                                    <li key={`${conflict.source?.kind ?? "project"}:${conflict.uid}`} className="space-y-1 break-words">
                                        <div>{targetName}</div>
                                        {conflict.source && (
                                            <div className="text-sm">
                                                {t(`cloud.Source ${conflict.source.kind}`)}: {conflict.source.title}
                                            </div>
                                        )}
                                        {conflict.same_name && <div className="text-sm">{t("card.Same file name")}</div>}
                                        {conflict.same_content && <div className="text-sm">{t("card.Same file content")}</div>}
                                        {conflict.can_replace && (
                                            <Button variant="outline" onClick={() => upload.decide("replace", conflict.uid, conflict.source?.kind)}>
                                                {t("card.Replace this attachment", { name: targetName })}
                                            </Button>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                        <Dialog.Footer className="gap-2">
                            <Button variant="secondary" onClick={() => upload.decide("cancel")}>
                                {t("common.Cancel")}
                            </Button>
                            <Button onClick={() => upload.decide("add")}>{t("card.Add separate attachment")}</Button>
                        </Dialog.Footer>
                    </Dialog.Content>
                )}
            </Dialog.Root>
        </div>
    );
}
