import { NeutralVideoSkin, Video, VideoPlayer as OSSVideoPlayer } from "@videojs/react/video";
import { I18nProvider } from "@videojs/react/i18n";
import "@videojs/react/i18n/locales/ko/register";
import playerStyles from "@videojs/react/video/neutral-skin.css?raw";
import { type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import useProtectedMedia from "@/core/hooks/useProtectedMedia";
import useAttachmentStoryboard from "./useAttachmentStoryboard";

/** Shared OSS controls; attachment transport remains subject to the current session. */
export default function VideoPlayer({ src, children, className, controls: _controls, ...props }: ComponentProps<"video">) {
    const media = useProtectedMedia(src);
    const storyboard = useAttachmentStoryboard(src);
    const { i18n } = useTranslation();

    return (
        <I18nProvider locale={i18n.resolvedLanguage ?? i18n.language}>
            <style href="videojs-neutral-skin" precedence="video-player">
                {playerStyles}
            </style>
            <OSSVideoPlayer>
                <NeutralVideoSkin className={className} style={{ width: "100%", aspectRatio: "16 / 9" }}>
                    <Video {...props} src={media.src} playsInline>
                        {children}
                        {storyboard && <track kind="metadata" label="thumbnails" src={storyboard} default />}
                    </Video>
                </NeutralVideoSkin>
            </OSSVideoPlayer>
        </I18nProvider>
    );
}
