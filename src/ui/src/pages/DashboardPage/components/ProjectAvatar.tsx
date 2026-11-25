import { Project } from "@/core/models";
import Avatar from "@/components/base/Avatar";
import IconComponent from "@/components/base/IconComponent";
import { cn } from "@/core/utils/ComponentUtils";
import { projectIdentityColor } from "./projectIdentityColor";

export default function ProjectAvatar({ project, className }: { project: Project.TModel; className?: string }) {
    const avatar = project.useField("avatar");
    const title = project.useField("title");
    const initial = title.normalize().match(/[\p{L}\p{N}]/u)?.[0];
    return (
        <Avatar.Root
            aria-hidden="true"
            data-project-avatar=""
            className={cn("size-9 rounded-lg", className)}
            style={{ backgroundColor: projectIdentityColor(project.uid) }}
        >
            {avatar && <Avatar.Image src={avatar} alt="" className="object-cover" />}
            <Avatar.Fallback className="rounded-none bg-transparent font-semibold text-white">
                {initial ?? <IconComponent icon="folder-kanban" size="3" />}
            </Avatar.Fallback>
        </Avatar.Root>
    );
}
