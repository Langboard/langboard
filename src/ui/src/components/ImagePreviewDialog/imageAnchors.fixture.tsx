import "@/core/injection";
import { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DndProvider } from "react-dnd";
import { HTML5Backend } from "react-dnd-html5-backend";
import { AuthUser, ProjectCard, ProjectCardAttachment, ProjectCardComment } from "@/core/models";
import { BoardCardProvider, useBoardCardPanel, useBoardCard } from "@/core/providers/BoardCardProvider";
import BoardCommentForm from "@/pages/BoardPage/components/card/comment/BoardCommentForm";
import BoardComment from "@/pages/BoardPage/components/card/comment/BoardComment";
import ImageThumbnail from "./ImageThumbnail";
import ImagePreviewDialog from ".";
import { createPortal } from "react-dom";
import { api } from "@/core/helpers/Api";
import { getCardCommentDraftStore } from "@/core/stores/CardCommentDraftStore";
import { useNativeImageComments } from "./NativeImageCommentContext";
import { isImageCommentAnchor } from "@/core/models/types/card-comment-anchor.type";
import i18n from "@/i18n";
void i18n.changeLanguage("en-US");
import "@/assets/styles/main.css";

const dates = { created_at: new Date(), updated_at: new Date() };
const user = AuthUser.Model.fromOne({
    ...dates,
    uid: "image-author",
    type: "user",
    firstname: "Image",
    lastname: "Author",
    username: "author",
    preferred_lang: "en-US",
});
const card = ProjectCard.Model.fromOne({
    ...dates,
    uid: "image-card",
    project_uid: "image-project",
    title: "Image card",
    visibility: "PRIVATE",
    count_comment: 1,
    current_auth_role_actions: ["*"],
    project_members: [],
});
const url = `${window.location.origin}/image-anchor.svg`;
const attachment = ProjectCardAttachment.Model.fromOne({
    ...dates,
    uid: "image-file",
    card_uid: card.uid,
    name: "Evidence.svg",
    url,
    image_revision: "a".repeat(64),
    order: 0,
});
let fail = false;
let sequence = 0;
const receipts = new Map<string, { uid: string; payload: string }>();
api.defaults.adapter = async (config) => {
    if (config.method === "put" && config.url?.includes("/comment/")) {
        document.getElementById("request")!.textContent = config.data;
        if (fail) {
            fail = false;
            throw new Error("Comment update failed fixture");
        }
        return { data: { updated_at: new Date().toISOString() }, status: 200, statusText: "OK", headers: {}, config };
    }
    if (config.method === "post" && config.url?.includes("/comment")) {
        const body = JSON.parse(config.data);
        document.getElementById("request")!.textContent = JSON.stringify(body);
        const previous = body.request_uid ? receipts.get(body.request_uid) : undefined;
        if (previous && previous.payload !== JSON.stringify(body)) throw new Error("Idempotency payload mismatch");
        const uid = previous?.uid ?? `saved-${++sequence}`;
        const replayed = !!previous;
        if (body.request_uid) receipts.set(body.request_uid, { uid, payload: JSON.stringify(body) });
        if (!replayed)
            ProjectCardComment.Model.fromOne(
                {
                    ...dates,
                    uid,
                    card_uid: card.uid,
                    content: { content: body.content },
                    anchor: body.anchor,
                    is_edited: false,
                    reactions: {},
                    user: { ...dates, uid: user.uid, type: "user", firstname: "Image", lastname: "Author", username: "author" },
                },
                true
            );
        if (fail) {
            fail = false;
            throw new Error("Ambiguous response fixture");
        }
        return { data: { comment_uid: uid, replayed }, status: 200, statusText: "OK", headers: {}, config };
    }
    return { data: { comments: [], entries: [], bots: [] }, status: 200, statusText: "OK", headers: {}, config };
};
const otherUser = AuthUser.Model.fromOne({
    ...dates,
    uid: "other-author",
    type: "user",
    firstname: "Other",
    lastname: "Author",
    username: "other",
    preferred_lang: "en-US",
});
const otherCard = ProjectCard.Model.fromOne({
    ...dates,
    uid: "other-card",
    project_uid: "image-project",
    title: "Other card",
    visibility: "PRIVATE",
    count_comment: 1,
    current_auth_role_actions: ["*"],
    project_members: [],
});
function Surface() {
    const { currentUser, card: currentCard, anchoredCommentRef } = useBoardCard();
    const { commentSurfaceRef } = useBoardCardPanel();
    const native = useNativeImageComments();
    const comments = ProjectCardComment.Model.useModels((model) => model.card_uid === currentCard.uid, [currentCard.uid]);
    const [draft, setDraft] = useState("");
    const [galleryOpen, setGalleryOpen] = useState(false);
    return (
        <div ref={commentSurfaceRef} className="min-w-0 p-8">
            <ImageThumbnail src={url} alt="Native evidence" />
            <ImageThumbnail src={`${url}?generic=true`} alt="Generic evidence" />
            <button onClick={() => setGalleryOpen(true)}>Image gallery</button>
            {galleryOpen &&
                createPortal(
                    <ImagePreviewDialog
                        files={[
                            { name: "First evidence", url, type: "image/svg+xml" },
                            { name: "Second evidence", url: `${url}?second=true`, type: "image/svg+xml" },
                        ]}
                        initialIndex={0}
                        onClose={() => setGalleryOpen(false)}
                    />,
                    document.body
                )}
            <div data-card-description>
                <p data-slate-node="element">Text evidence selection</p>
            </div>
            <button
                onClick={() =>
                    anchoredCommentRef.current({
                        type: "TextQuoteSelector",
                        version: 1,
                        exact: "Text evidence",
                        prefix: "",
                        suffix: " selection",
                        start_block: "Text evidence selection",
                        end_block: "Text evidence selection",
                        start_path: [0],
                        end_path: [0],
                    })
                }
            >
                Text anchor
            </button>
            {comments.map((comment) => (
                <div key={comment.uid}>
                    <BoardComment comment={comment} deletedComment={(uid) => ProjectCardComment.Model.deleteModel(uid)} />
                    <button
                        onClick={() => {
                            if (isImageCommentAnchor(comment.anchor)) native?.jump(comment.anchor, comment.uid);
                        }}
                    >
                        Jump saved {comment.uid}
                    </button>
                </div>
            ))}
            <BoardCommentForm variant="panel" />
            <BoardCommentForm variant="mobile" />
            <button
                onClick={() => {
                    currentCard.current_auth_role_actions = ["Read"];
                }}
            >
                Read only
            </button>
            <button
                onClick={() => {
                    currentUser.is_admin = !currentUser.is_admin;
                }}
            >
                Toggle admin
            </button>
            <button
                onClick={() => {
                    fail = true;
                }}
            >
                Fail next response
            </button>
            <button
                onClick={() =>
                    ProjectCardAttachment.Model.fromOne({ uid: attachment.uid, image_revision: "b".repeat(64) } as ProjectCardAttachment.Interface)
                }
            >
                Replace image
            </button>
            <button onClick={() => ProjectCardAttachment.Model.deleteModel(attachment.uid)}>Delete image</button>
            <button
                onClick={() => setDraft(JSON.stringify(getCardCommentDraftStore().getSnapshot(currentUser.uid, "image-project", currentCard.uid)))}
            >
                Inspect draft
            </button>
            <output id="draft">{draft}</output>
            <output id="request" />
        </div>
    );
}
function Fixture() {
    const ref = useRef<HTMLDivElement>(null);
    const [actor, setActor] = useState(user);
    const [activeCard, setActiveCard] = useState(card);
    return (
        <main ref={ref}>
            <button onClick={() => setActor((previous) => (previous.uid === user.uid ? otherUser : user))}>Switch actor</button>
            <button onClick={() => setActiveCard((previous) => (previous.uid === card.uid ? otherCard : card))}>Switch card</button>
            <BoardCardProvider projectUID="image-project" card={activeCard} currentUser={actor} viewportRef={ref}>
                <Surface />
            </BoardCardProvider>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <DndProvider backend={HTML5Backend}>
                <Fixture />
            </DndProvider>
        </MemoryRouter>
    </QueryClientProvider>
);
