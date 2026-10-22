import IconComponent from "@/components/base/IconComponent";
import type { IStore as ProjectCard } from "@/core/models/ProjectCard";
import { cn } from "@/core/utils/ComponentUtils";
import { formatTimerDuration } from "@/core/utils/LocaleFormat";
import { useEffect, useReducer, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

type ActiveWorker = NonNullable<ProjectCard["active_workers"]>[number];

interface Props {
    avatar: ReactNode;
    worker: ActiveWorker | undefined;
    startedLabel: string;
    pausedLabel: string;
}

export default function BoardActiveWorkerAvatar({ worker, ...props }: Props) {
    return worker ? <ActiveAvatar key={worker.user_uid} worker={worker} {...props} /> : <>{props.avatar}</>;
}

function ActiveAvatar({ avatar, worker, startedLabel, pausedLabel }: Props & { worker: ActiveWorker }) {
    const { i18n } = useTranslation();
    const [hovered, setHovered] = useState(false);
    const [focused, setFocused] = useState(false);
    const [, tick] = useReducer((value) => value + 1, 0);
    const inspecting = hovered || focused;
    useEffect(() => {
        if (!inspecting || worker.status !== "started") return;
        let timer: number | undefined;
        const start = () => {
            if (!document.hidden) timer = window.setInterval(tick, 1000);
        };
        const stop = () => {
            window.clearInterval(timer);
            timer = undefined;
        };
        const visibilityChanged = () => {
            stop();
            tick();
            start();
        };
        start();
        document.addEventListener("visibilitychange", visibilityChanged);
        return () => {
            stop();
            document.removeEventListener("visibilitychange", visibilityChanged);
        };
    }, [inspecting, worker.status]);
    const running = worker.status === "started";
    const now = Date.now();
    const title = `${running ? startedLabel : pausedLabel}: ${worker.checkitems
        .map((item) => {
            const seconds =
                item.elapsed_seconds + (item.status === "started" ? Math.max(0, Math.floor((now - Date.parse(item.sampled_at)) / 1000)) : 0);
            return `${item.title} · ${formatTimerDuration(
                { hours: Math.floor(seconds / 3600), minutes: Math.floor((seconds % 3600) / 60), seconds: seconds % 60 },
                i18n.language
            )}`;
        })
        .join(", ")}`;
    return (
        <span
            className="relative inline-flex shrink-0"
            title={title}
            aria-label={title}
            tabIndex={0}
            onPointerEnter={() => setHovered(true)}
            onPointerLeave={() => setHovered(false)}
            onFocus={() => setFocused(true)}
            onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
            }}
        >
            {avatar}
            <span
                className={cn(
                    "pointer-events-none absolute -bottom-1 -right-1 inline-flex size-4 items-center",
                    "justify-center rounded-full border border-background",
                    running ? "bg-emerald-600 text-white" : "bg-muted text-muted-foreground"
                )}
            >
                <IconComponent icon={running ? "hammer" : "pause"} size="3" aria-hidden="true" />
            </span>
        </span>
    );
}
