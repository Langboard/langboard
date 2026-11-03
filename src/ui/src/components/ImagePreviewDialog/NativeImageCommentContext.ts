import { createContext, useContext } from "react";
import type { ICardCommentImageAnchor } from "@/core/models/types/card-comment-anchor.type";
import type { ImageAnnotationContext } from "./ImageAnnotations";
export interface NativeImageComments {
    contextForURL: (url: string | undefined, close: () => void) => ImageAnnotationContext | undefined;
    jump: (anchor: ICardCommentImageAnchor, commentUID: string) => void;
    isCurrent: (anchor: ICardCommentImageAnchor) => boolean;
}
export const NativeImageContext = createContext<NativeImageComments | null>(null);
export const useNativeImageComments = () => useContext(NativeImageContext);
