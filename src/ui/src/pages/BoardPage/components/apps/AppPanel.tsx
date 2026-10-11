import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { useTranslation } from "react-i18next";
import Button from "@/components/base/Button";
import { api } from "@/core/helpers/Api";
import type { CatalogApp } from "@/controllers/api/board/useBoardAppCatalog";
import { loadPanelResources, readPanelDesign } from "@/core/apps/PanelDesign";
import { activePanelDisposals, panelStateCache } from "@/core/apps/PanelSession";
import { createPanelHost, type JsonValue } from "@langboard/app-panel";

import type { PanelInteractionTarget } from "./PanelInteractionDialog";
const CardAppRuntimes = lazy(() => import("../card/CardAppRuntimes"));
import Dialog from "@/components/base/Dialog";
const PanelInteractionDialog = lazy(() => import("./PanelInteractionDialog"));

interface Props {
    app: CatalogApp;
    projectUID: string;
    userUID: string;
    onClose: () => void;
}
interface PanelPermission {
    key: string;
    version: string;
    panel: { url: string; name: string };
    app_revision: string;
    binding_revision: string;
}

// A live frame belongs to one permission read and one visible document session.
export function useAppPanel({ app, projectUID, userUID, onClose }: Props) {
    const [, i18n] = useTranslation();
    const language = i18n.resolvedLanguage ?? i18n.language;
    const container = useRef<HTMLDivElement>(null);
    const closeRef = useRef(onClose);
    closeRef.current = onClose;
    const [intersecting, setIntersecting] = useState(false);
    const [visible, setVisible] = useState(!document.hidden);
    const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
    const [runtimeTarget, setRuntimeTarget] = useState<{ card_uid: string; app_revision: string; binding_revision: string } | null>(null);
    const [interaction, setInteraction] = useState<PanelInteractionTarget | null>(null);
    const interactionOpen = useRef(false);
    const closeInteraction = () => {
        interactionOpen.current = false;
        setInteraction(null);
    };
    const failRef = useRef<() => void>(() => {});
    const failedScope = useRef<string | null>(null);
    useEffect(() => {
        const update = () => setVisible(!document.hidden);
        document.addEventListener("visibilitychange", update);
        return () => document.removeEventListener("visibilitychange", update);
    }, []);
    useEffect(() => {
        const observer = new IntersectionObserver(([entry]) => {
            if (!interactionOpen.current)
                setIntersecting(entry.isIntersecting && entry.intersectionRect.width > 0 && entry.intersectionRect.height > 0);
        });
        if (container.current) observer.observe(container.current);
        return () => observer.disconnect();
    }, []);
    useEffect(() => {
        const scope = JSON.stringify([userUID, projectUID, app.key, app.version]);
        if (!visible || !intersecting || !container.current || failedScope.current === scope) return;
        const controller = new AbortController();
        let frame: HTMLIFrameElement | undefined;
        let loadTimer: ReturnType<typeof setTimeout> | undefined;
        let disposeHost: ReturnType<typeof createPanelHost> | undefined;
        let themeObserver: MutationObserver | undefined;
        let stateKey = JSON.stringify([userUID, projectUID, app.key, app.version]);
        const teardown = () => {
            interactionOpen.current = false;
            setInteraction(null);
            setRuntimeTarget(null);
            controller.abort();
            clearTimeout(loadTimer);
            themeObserver?.disconnect();
            disposeHost?.();
            frame?.remove();
        };
        const fail = () => {
            failedScope.current = scope;
            activePanelDisposals.delete(stateKey);
            panelStateCache.delete(stateKey);
            teardown();
            setStatus("error");
        };
        failRef.current = fail;
        activePanelDisposals.set(stateKey, fail);
        const onHidden = () => {
            if (document.hidden) teardown();
        };
        document.addEventListener("visibilitychange", onHidden);
        setStatus("loading");
        void api
            .get<PanelPermission>(`/board/${projectUID}/apps/${encodeURIComponent(app.key)}/panel`, {
                signal: controller.signal,
                env: { interceptToast: true } as never,
            })
            .then(async ({ data }) => {
                const resources = await loadPanelResources(controller.signal);
                if (controller.signal.aborted || document.hidden || !container.current) return;
                activePanelDisposals.delete(stateKey);
                stateKey = JSON.stringify([userUID, projectUID, app.key, data.version]);
                activePanelDisposals.set(stateKey, fail);
                frame = document.createElement("iframe");
                frame.setAttribute("sandbox", "allow-scripts allow-forms");
                frame.referrerPolicy = "no-referrer";
                frame.title = data.panel.name;
                frame.className = "size-full border-0";
                frame.addEventListener(
                    "load",
                    () => {
                        if (controller.signal.aborted || document.hidden || !frame) return;
                        try {
                            disposeHost = createPanelHost({
                                frame,
                                context: { project_uid: projectUID, app_key: app.key, app_version: data.version, language },
                                state: panelStateCache.get(stateKey),
                                design: { ...readPanelDesign(), resources },
                                onRequest: async (operation: string, params: JsonValue, signal: AbortSignal) => {
                                    try {
                                        if (!params || typeof params !== "object" || Array.isArray(params))
                                            throw new Error("Invalid panel request parameters");
                                        if (operation === "runtimes.open") {
                                            if (
                                                Object.keys(params).some((key) => key !== "card_uid") ||
                                                typeof params.card_uid !== "string" ||
                                                !/^[A-Za-z0-9]{11}$/.test(params.card_uid) ||
                                                interactionOpen.current ||
                                                signal.aborted ||
                                                controller.signal.aborted
                                            )
                                                throw new Error("Invalid runtime dialog request");
                                            interactionOpen.current = true;
                                            setRuntimeTarget({
                                                card_uid: params.card_uid,
                                                app_revision: data.app_revision,
                                                binding_revision: data.binding_revision,
                                            });
                                            return { state: "opened" };
                                        }
                                        if (operation === "interactions.open") {
                                            if (
                                                Object.keys(params).some((key) => key !== "card_uid" && key !== "interaction_uid") ||
                                                typeof params.card_uid !== "string" ||
                                                !/^[A-Za-z0-9]{11}$/.test(params.card_uid) ||
                                                typeof params.interaction_uid !== "string" ||
                                                !/^[A-Za-z0-9]{11}$/.test(params.interaction_uid) ||
                                                interactionOpen.current ||
                                                signal.aborted ||
                                                controller.signal.aborted
                                            )
                                                throw new Error("Invalid interaction request parameters");
                                            interactionOpen.current = true;
                                            setInteraction({
                                                card_uid: params.card_uid,
                                                interaction_uid: params.interaction_uid,
                                                app_revision: data.app_revision,
                                                binding_revision: data.binding_revision,
                                            });
                                            return { state: "opened", interaction_uid: params.interaction_uid };
                                        }
                                        let endpoint: "resources" | "signals" | "interactions";
                                        if (operation === "resources.list") {
                                            if (
                                                Object.keys(params).some((key) => key !== "cursor") ||
                                                (params.cursor !== undefined &&
                                                    params.cursor !== null &&
                                                    (typeof params.cursor !== "string" || !/^[A-Za-z0-9_-]{23}$/.test(params.cursor)))
                                            )
                                                throw new Error("Invalid resource request parameters");
                                            endpoint = "resources";
                                        } else if (operation === "signals.list") {
                                            if (
                                                Object.keys(params).some((key) => key !== "provider" && key !== "after") ||
                                                typeof params.provider !== "string" ||
                                                !/^[a-z][a-z0-9_-]{0,31}$/.test(params.provider) ||
                                                (params.after !== undefined && typeof params.after !== "string")
                                            )
                                                throw new Error("Invalid signal request parameters");
                                            endpoint = "signals";
                                        } else if (operation === "interactions.list") {
                                            if (
                                                Object.keys(params).some(
                                                    (key) => key !== "card_uid" && key !== "cursor" && key !== "limit" && key !== "interaction_uid"
                                                ) ||
                                                typeof params.card_uid !== "string" ||
                                                !/^[A-Za-z0-9]{11}$/.test(params.card_uid) ||
                                                (params.cursor !== undefined &&
                                                    params.cursor !== null &&
                                                    (typeof params.cursor !== "string" || !/^[A-Za-z0-9]{11}$/.test(params.cursor))) ||
                                                (params.interaction_uid !== undefined &&
                                                    params.interaction_uid !== null &&
                                                    (typeof params.interaction_uid !== "string" ||
                                                        !/^[A-Za-z0-9]{11}$/.test(params.interaction_uid))) ||
                                                (params.limit !== undefined &&
                                                    (!Number.isInteger(params.limit) || Number(params.limit) < 1 || Number(params.limit) > 25))
                                            )
                                                throw new Error("Invalid interaction request parameters");
                                            endpoint = "interactions";
                                        } else throw new Error("Unsupported panel operation");
                                        const response = await api.post<JsonValue>(
                                            `/board/${projectUID}/apps/${encodeURIComponent(app.key)}/panel/${endpoint}`,
                                            { ...params, app_revision: data.app_revision, binding_revision: data.binding_revision },
                                            { signal: AbortSignal.any([signal, controller.signal]), env: { interceptToast: true } as never }
                                        );
                                        return response.data;
                                    } catch (error) {
                                        // An authority denial invalidates this pinned frame immediately.
                                        if (isAxiosError(error) && [401, 403, 404, 409].includes(error.response?.status ?? 0)) fail();
                                        throw error;
                                    }
                                },
                                onState: (state) => panelStateCache.set(stateKey, state),
                                onReady: () => {
                                    clearTimeout(loadTimer);
                                    setStatus("ready");
                                },
                                onClose: () => {
                                    teardown();
                                    closeRef.current();
                                },
                                onError: fail,
                            });
                            themeObserver = new MutationObserver(() => {
                                if (!controller.signal.aborted) disposeHost?.updateDesign(readPanelDesign());
                            });
                            themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
                        } catch {
                            fail();
                        }
                    },
                    { once: true }
                );
                loadTimer = setTimeout(fail, 10_000);
                frame.src = data.panel.url;
                container.current.append(frame);
            })
            .catch(() => {
                if (!controller.signal.aborted) fail();
            });
        return () => {
            activePanelDisposals.delete(stateKey);
            document.removeEventListener("visibilitychange", onHidden);
            teardown();
        };
    }, [visible, intersecting, language, projectUID, userUID, app.key, app.version, app.panel?.url, app.app_revision, app.binding?.revision]);
    return {
        container,
        status,
        interaction,
        runtimeTarget,
        closeRuntime: () => {
            interactionOpen.current = false;
            setRuntimeTarget(null);
        },
        closeInteraction,
        denyPanel: () => failRef.current(),
    };
}

export default function AppPanel(props: Props) {
    const [t] = useTranslation();
    const { container, status, interaction, runtimeTarget, closeRuntime, closeInteraction, denyPanel } = useAppPanel(props);
    return (
        <div className="flex h-full min-h-0 flex-col" role="region" aria-label={props.app.panel?.name ?? props.app.name}>
            <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
                <span className="truncate text-sm font-medium">{props.app.panel?.name ?? props.app.name}</span>
                <Button size="icon" variant="ghost" onClick={props.onClose} aria-label={t("project.Close App panel")}>
                    ×
                </Button>
            </div>
            {status === "loading" && (
                <p className="p-3 text-sm" role="status">
                    {t("common.Loading...")}
                </p>
            )}
            {status === "error" && (
                <p className="p-3 text-sm" role="alert">
                    {t("project.App panel unavailable")}
                </p>
            )}
            <div ref={container} className="min-h-0 flex-1" />
            {runtimeTarget && (
                <Dialog.Root
                    open
                    onOpenChange={(open) => {
                        if (!open) closeRuntime();
                    }}
                >
                    <Dialog.Content className="max-w-lg">
                        <Dialog.Header>
                            <Dialog.Title>{t("card.appRuntimes.title")}</Dialog.Title>
                            <Dialog.Description>{t("card.appRuntimes.help")}</Dialog.Description>
                        </Dialog.Header>
                        <Suspense fallback={null}>
                            <CardAppRuntimes
                                projectUID={props.projectUID}
                                cardUID={runtimeTarget.card_uid}
                                userUID={props.userUID}
                                canRespond
                                panelContext={{ appKey: props.app.key, ...runtimeTarget }}
                                onAuthorityDenied={denyPanel}
                            />
                        </Suspense>
                    </Dialog.Content>
                </Dialog.Root>
            )}
            {interaction && (
                <Suspense fallback={null}>
                    <PanelInteractionDialog
                        projectUID={props.projectUID}
                        userUID={props.userUID}
                        appKey={props.app.key}
                        target={interaction}
                        onClose={closeInteraction}
                        onAuthorityDenied={denyPanel}
                    />
                </Suspense>
            )}
        </div>
    );
}
