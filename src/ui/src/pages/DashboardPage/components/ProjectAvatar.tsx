import { Project } from "@/core/models";
import IconComponent from "@/components/base/IconComponent";
export default function ProjectAvatar({ project, className }: { project: Project.TModel; className?: string }) {
    const avatar = project.useField("avatar");
    return avatar ? (
        <img src={avatar} alt="" className={className ?? "size-9 rounded-lg object-cover"} />
    ) : (
        <IconComponent icon="folder-kanban" size="4" />
    );
}
