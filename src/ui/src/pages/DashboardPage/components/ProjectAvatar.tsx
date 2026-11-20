import { Project } from "@/core/models";
import Avatar from "@/components/base/Avatar";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { projectIdentityColor } from "@/core/models/projectIdentity";

export default function ProjectAvatar({ project, className }: { project: Project.TModel; className?: string }) {
    const avatar = project.useField("avatar");
    const title = project.useField("title");
    const identity = project.useField("identity");
    const style = identity?.style ?? "auto";
    const showLogo = avatar && (style === "auto" || style === "logo");
    const initial = title.normalize().match(/[\p{L}\p{N}]/u)?.[0];
    const abbreviation = style === "abbreviation" ? identity?.abbreviation : null;
    return (
        <Avatar.Root
            aria-hidden="true"
            data-project-avatar=""
            className={cn("size-9 rounded-lg", className)}
            style={{ backgroundColor: projectIdentityColor(project.uid, identity?.color) }}
        >
            {showLogo && <Avatar.Image src={avatar} alt="" className="object-cover" />}
            <Avatar.Fallback className="rounded-none bg-transparent font-semibold text-white">
                {style === "icon" ? (
                    <IconComponent icon={identity?.icon ?? "folder-kanban"} size="4" />
                ) : abbreviation ? (
                    <span className="max-w-full truncate px-0.5 text-[0.7em]">{abbreviation}</span>
                ) : (
                    (initial ?? <IconComponent icon="folder-kanban" size="3" />)
                )}
            </Avatar.Fallback>
        </Avatar.Root>
    );
}
