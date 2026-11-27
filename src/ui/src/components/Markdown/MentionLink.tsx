import Badge from "@/components/base/Badge";
import IconComponent from "@/components/base/IconComponent";
import { BotModel, User } from "@/core/models";

export interface IMarkdownMentionLinkProps {
    fallbackLabel: string;
    uid: string;
}

function MarkdownMentionLink({ fallbackLabel, uid }: IMarkdownMentionLinkProps) {
    const user = User.Model.useModel(uid, [uid]);
    const bot = BotModel.Model.useModel(uid, [uid]);

    if (user) {
        return <MarkdownUserMention user={user} fallbackLabel={fallbackLabel} />;
    }

    if (bot) {
        return <MarkdownBotMention bot={bot} fallbackLabel={fallbackLabel} />;
    }

    return <MarkdownMentionBadge icon="at-sign" label={fallbackLabel} />;
}

function MarkdownUserMention({ fallbackLabel, user }: { fallbackLabel: string; user: User.TModel }) {
    const firstname = user.useField("firstname");
    const lastname = user.useField("lastname");
    const username = user.useField("username");
    const label = `${firstname} ${lastname}`.trim() || username || fallbackLabel;

    return <MarkdownMentionBadge icon="user" label={`@${label.replace(/^@/, "")}`} />;
}

function MarkdownBotMention({ fallbackLabel, bot }: { fallbackLabel: string; bot: BotModel.TModel }) {
    const name = bot.useField("name");
    const label = name || fallbackLabel;

    return <MarkdownMentionBadge icon="bot" label={`@${label.replace(/^@/, "")}`} />;
}

function MarkdownMentionBadge({ icon, label }: { icon: string; label: string }) {
    return (
        <Badge variant="secondary" className="mx-0.5 inline-flex max-w-full items-center gap-1 align-baseline">
            <IconComponent icon={icon} size="3.5" className="shrink-0" />
            <span className="truncate">{label}</span>
        </Badge>
    );
}

export default MarkdownMentionLink;
