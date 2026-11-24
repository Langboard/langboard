import { ProjectCard, ProjectCardAttachment, ProjectCardComment, ProjectChecklist } from "@/core/models";
import { useBoardCard } from "@/core/providers/BoardCardProvider";
import WhisperDiscussions, { WhisperAnchor } from "./WhisperDiscussions";
import { useTranslation } from "react-i18next";

export default function BoardCardWhispers() {
    const { card, currentUser, projectUID } = useBoardCard();
    const [t] = useTranslation();
    const members = card.useForeignFieldArray("project_members");
    const relationships = card.useForeignFieldArray("relationships");
    const blocks = card.useField("content_blocks") ?? [];
    const checklists = ProjectChecklist.Model.useModels((item) => item.card_uid === card.uid, [card]);
    const attachments = ProjectCardAttachment.Model.useModels((item) => item.card_uid === card.uid, [card]);
    const comments = ProjectCardComment.Model.useModels((item) => item.card_uid === card.uid, [card]);
    const anchors: WhisperAnchor[] = [
        { key: "card", label: t("card.whispers.card"), form: {} },
        ...relationships.map((relation) => {
            const otherUID = relation.parent_card_uid === card.uid ? relation.child_card_uid : relation.parent_card_uid;
            return {
                key: `relationship:${relation.uid}`,
                label: `${t("card.whispers.relationship")} · ${ProjectCard.Model.getModel(otherUID)?.title || otherUID}`,
                form: { relationship_uid: relation.uid },
            };
        }),
        ...blocks.map((block) => ({
            key: `content_block:${block.block_uid}`,
            label: `${t("card.whispers.content_block")} · ${block.order + 1}`,
            form: { subanchor_type: "content_block" as const, subanchor_uid: block.block_uid },
        })),
        ...checklists.map((item) => ({
            key: `checklist:${item.uid}`,
            label: `${t("card.whispers.checklist")} · ${item.title}`,
            form: { subanchor_type: "checklist" as const, subanchor_uid: item.uid },
        })),
        ...attachments.map((item) => ({
            key: `attachment:${item.uid}`,
            label: `${t("card.whispers.attachment")} · ${item.name}`,
            form: { subanchor_type: "attachment" as const, subanchor_uid: item.uid },
        })),
        ...comments.map((item) => ({
            key: `comment:${item.uid}`,
            label: `${t("card.whispers.comment")} · ${item.uid}`,
            form: { subanchor_type: "comment" as const, subanchor_uid: item.uid },
        })),
    ];
    return (
        <WhisperDiscussions
            projectUID={projectUID}
            cardUID={card.uid}
            userUID={currentUser.uid}
            anchors={anchors}
            people={members.map((user) => ({
                uid: user.uid,
                name: `${user.firstname} ${user.lastname}`.trim(),
                external: user.membership_classification === "external",
            }))}
        />
    );
}
