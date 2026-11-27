import ControlOverlay from "@/components/Collaborative/ControlOverlay";
import Input from "@/components/Collaborative/Input";
import Textarea from "@/components/Collaborative/Textarea";
import UserLabel from "@/components/Collaborative/UserLabel";

export { ControlOverlay, Input, Textarea, UserLabel };
export type { ICollaborativeControlOverlayProps } from "@/components/Collaborative/ControlOverlay";
export { useCollaborativeText } from "@/components/Collaborative/useCollaborativeText";
export type { ICollaborativeInputProps } from "@/components/Collaborative/Input";
export type { ICollaborativeTextareaProps } from "@/components/Collaborative/Textarea";
export type { ICollaborativeUserLabelProps } from "@/components/Collaborative/UserLabel";
export type { IUseCollaborativeTextProps } from "@/components/Collaborative/useCollaborativeText";

const Collaborative = {
    ControlOverlay,
    Input,
    Textarea,
    UserLabel,
};

export default Collaborative;
