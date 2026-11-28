import { Project } from "@/core/models";
import BoardCloud from "./components/cloud/BoardCloud";

export default function BoardCloudPage({ project }: { project: Project.TModel }) {
    return <BoardCloud key={project.uid} projectUID={project.uid} />;
}
