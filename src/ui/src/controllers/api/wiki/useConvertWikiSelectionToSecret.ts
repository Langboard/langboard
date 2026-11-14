import { secretReferenceHistoryHref } from "@/core/utils/SecretReferenceLink";
import { api } from "@/core/helpers/Api";
import { useQueryMutation } from "@/core/helpers/QueryMutation";
import useAuthStore from "@/core/stores/AuthStore";
import { Routing } from "@langboard/core/constants";
import { Utils } from "@langboard/core/utils";
import { wikiMarkdownRevision, type WikiSecretSelection } from "@/components/Editor/wiki-secret-selection";
import type { IEditorContent } from "@/core/models/Base";

export interface ConvertWikiSelectionForm {
    project_uid: string;
    wiki_uid: string;
    expected_revision: string;
    name: string;
    selection: WikiSecretSelection;
}
interface ConversionResult {
    wiki_uid: string;
    revision: string;
    editor_generation: number;
    reference: { uri: string; name: string; scope: "project"; scope_uid: string; state: "active"; revision: number };
}

export async function convertWikiSelectionToSecret(form: ConvertWikiSelectionForm, isCurrent: () => boolean) {
    const auth = useAuthStore.getState();
    const actor = auth.currentUser?.uid;
    const session = auth.getSessionVersion();
    const token = auth.getToken();
    const identity = auth.getIdentityVersion();
    const check = () => {
        const now = useAuthStore.getState();
        if (
            !actor ||
            !isCurrent() ||
            now.currentUser?.uid !== actor ||
            now.getSessionVersion() !== session ||
            now.getToken() !== token ||
            now.getIdentityVersion() !== identity
        )
            throw new Error("wiki_context_changed");
    };
    check();
    const ids = { uid: form.project_uid, wiki_uid: form.wiki_uid };
    const options = { env: { interceptToast: true } as never };
    const { data: result } = await api.post<ConversionResult>(
        Utils.String.format("/board/{uid}/wiki/{wiki_uid}/secret-selection", ids),
        {
            expected_revision: form.expected_revision,
            name: form.name,
            selection: { start_utf16: form.selection.start_utf16, end_utf16: form.selection.end_utf16 },
        },
        options
    );
    check();
    if (
        result.wiki_uid !== form.wiki_uid ||
        !secretReferenceHistoryHref(result.reference?.uri) ||
        result.reference.scope !== "project" ||
        result.reference.scope_uid !== form.project_uid ||
        result.reference.state !== "active" ||
        result.reference.revision !== 0 ||
        result.reference.name !== form.name
    )
        throw new Error("wiki_result_unavailable");
    const { data } = await api.get<{ wiki: { uid: string; content: IEditorContent; editor_generation: number } }>(
        Utils.String.format(Routing.API.BOARD.WIKI.GET_DETAILS, ids),
        options
    );
    check();
    const content = data.wiki?.content;
    if (
        data.wiki?.uid !== form.wiki_uid ||
        typeof content?.content !== "string" ||
        !Number.isSafeInteger(result.editor_generation) ||
        data.wiki.editor_generation !== result.editor_generation ||
        (await wikiMarkdownRevision(content.content)) !== result.revision ||
        !content.content.includes(`[••••](${result.reference.uri})`)
    )
        throw new Error("wiki_result_unavailable");
    check();
    return { content, editor_generation: result.editor_generation };
}

export default function useConvertWikiSelectionToSecret() {
    const { mutate } = useQueryMutation();
    return mutate(
        ["convert-wiki-selection-to-secret"],
        ({ form, isCurrent }: { form: ConvertWikiSelectionForm; isCurrent: () => boolean }) => convertWikiSelectionToSecret(form, isCurrent),
        { retry: 0 }
    );
}
