export interface ICardCommentTextAnchor {
    type?: "TextQuoteSelector";
    version: 1;
    exact: string;
    prefix: string;
    suffix: string;
    start_block: string;
    end_block: string;
    start_path: number[];
    end_path: number[];
}

export interface ICardCommentImageAnchor {
    type: "ImageRegionSelector";
    version: 1;
    source: { type: "card_attachment"; attachment_uid: string; revision: string };
    shape: "point" | "rectangle";
    image_width: number;
    image_height: number;
    x: number;
    y: number;
    width: number;
    height: number;
}

export type ICardCommentAnchor = ICardCommentTextAnchor | ICardCommentImageAnchor;
export const isImageCommentAnchor = (anchor: ICardCommentAnchor | null | undefined): anchor is ICardCommentImageAnchor =>
    anchor?.type === "ImageRegionSelector";
