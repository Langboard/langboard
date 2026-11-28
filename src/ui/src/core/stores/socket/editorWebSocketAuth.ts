import { Utils } from "@langboard/core/utils";

export const getEditorWebSocketAuth = (authorizedUrl: string, documentID: string) => {
    const url = new URL(authorizedUrl);
    const token = url.searchParams.get("authorization");
    if (!token) {
        return null;
    }

    url.searchParams.delete("authorization");
    url.searchParams.set("route_key", Utils.String.createEditorDocumentRouteKey(documentID));
    return { url: url.toString(), token };
};
