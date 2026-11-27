import Badge from "@/components/base/Badge";
import IconComponent from "@/components/base/IconComponent";
import { ProjectCard, ProjectWiki } from "@/core/models";
import { ROUTES } from "@/core/routing/constants";
import { cn } from "@/core/utils/ComponentUtils";

export interface IMarkdownInternalLinkProps {
    internalType?: string;
    uid?: string;
}

function MarkdownInternalLink({ internalType, uid }: IMarkdownInternalLinkProps) {
    if (!internalType || !uid) {
        return null;
    }

    switch (internalType) {
        case "card":
            return <MarkdownCardInternalLink uid={uid} />;
        case "project_wiki":
            return <MarkdownWikiInternalLink uid={uid} />;
        default:
            return <MarkdownUnknownInternalLink internalType={internalType} uid={uid} />;
    }
}

function MarkdownCardInternalLink({ uid }: { uid: string }) {
    const card = ProjectCard.Model.useModel(uid, [uid]);
    if (!card) {
        return <MarkdownInternalLinkBadge icon="credit-card" label={uid} />;
    }

    return <MarkdownCardInternalLinkDisplay card={card} />;
}

function MarkdownWikiInternalLink({ uid }: { uid: string }) {
    const wiki = ProjectWiki.Model.useModel(uid, [uid]);
    if (!wiki) {
        return <MarkdownInternalLinkBadge icon="brain" label={uid} />;
    }

    return <MarkdownWikiInternalLinkDisplay wiki={wiki} />;
}

function MarkdownCardInternalLinkDisplay({ card }: { card: ProjectCard.TModel }) {
    const title = card.useField("title");
    const projectUID = card.useField("project_uid");

    return <MarkdownInternalLinkBadge href={ROUTES.BOARD.CARD(projectUID, card.uid)} icon="credit-card" label={title} />;
}

function MarkdownWikiInternalLinkDisplay({ wiki }: { wiki: ProjectWiki.TModel }) {
    const title = wiki.useField("title");
    const projectUID = wiki.useField("project_uid");

    return <MarkdownInternalLinkBadge href={ROUTES.BOARD.WIKI_PAGE(projectUID, wiki.uid)} icon="brain" label={title} />;
}

function MarkdownUnknownInternalLink({ internalType, uid }: { internalType: string; uid: string }) {
    return <MarkdownInternalLinkBadge icon="circle-slash" label={`${internalType}:${uid}`} />;
}

function MarkdownInternalLinkBadge({ href, icon, label }: { href?: string; icon: string; label: string }) {
    const content = (
        <Badge variant="secondary" className="mx-0.5 inline-flex max-w-full items-center gap-1 align-baseline">
            <IconComponent icon={icon} size="3.5" className="shrink-0" />
            <span className="truncate">{label}</span>
        </Badge>
    );

    if (!href) {
        return content;
    }

    return (
        <a href={href} className={cn("inline-flex max-w-full align-baseline no-underline hover:underline")}>
            {content}
        </a>
    );
}

export default MarkdownInternalLink;
