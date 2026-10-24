import Badge from "@/components/base/Badge";
import Box from "@/components/base/Box";
import Button from "@/components/base/Button";
import Flex from "@/components/base/Flex";
import IconComponent from "@/components/base/IconComponent";
import Textarea from "@/components/base/Textarea";
import Toast from "@/components/base/Toast";
import useDraftBotFromInstruction, { IBotDraft } from "@/controllers/api/settings/bots/useDraftBotFromInstruction";
import { Utils } from "@langboard/core/utils";
import useAuthStore from "@/core/stores/AuthStore";
import type { AvatarGenerationRequest } from "@/components/AvatarGeneration";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface IBotCreateAssistantPanelProps {
    disabled?: bool;
    getCurrentValue: () => Record<string, unknown>;
    onApplyDraft: (draft: IBotDraft) => void;
    onRequestAvatar: (request: AvatarGenerationRequest) => void;
    getDraftIdentity: () => string;
    onPendingChange: (pending: boolean) => void;
}

interface IBotCreateAssistantMessage {
    role: "user" | "assistant";
    content: string;
}

function BotCreateAssistantPanel({
    disabled,
    getCurrentValue,
    onApplyDraft,
    onRequestAvatar,
    getDraftIdentity,
    onPendingChange,
}: IBotCreateAssistantPanelProps): React.JSX.Element {
    const [t] = useTranslation();
    const actor = useAuthStore((state) => state.currentUser);
    const authority = JSON.stringify([actor?.uid, actor?.is_admin, actor?.setting_role_actions]);
    const currentAuthority = () => {
        const user = useAuthStore.getState().currentUser;
        return JSON.stringify([user?.uid, user?.is_admin, user?.setting_role_actions]);
    };
    const request = useRef<AbortController | null>(null);
    const requestVersion = useRef(0);
    const latest = useRef({ disabled, getDraftIdentity, getCurrentValue, onApplyDraft });
    latest.current = { disabled, getDraftIdentity, getCurrentValue, onApplyDraft };
    useEffect(() => {
        return () => {
            ++requestVersion.current;
            request.current?.abort();
            onPendingChange(false);
        };
    }, [actor, authority, onPendingChange]);
    const [instruction, setInstruction] = useState("");
    const [messages, setMessages] = useState<IBotCreateAssistantMessage[]>([]);
    const { mutate, isPending } = useDraftBotFromInstruction({ interceptToast: true });
    const canSend = !!instruction.trim() && !disabled && !isPending;

    const submit = () => {
        const nextInstruction = instruction.trim();
        if (!canSend) {
            return;
        }

        const version = ++requestVersion.current;
        onRequestAvatar({
            id: version,
            prompt: nextInstruction,
            onGenerate: () => {
                if (version !== requestVersion.current || latest.current.disabled || useAuthStore.getState().currentUser !== actor) return;
                const identity = latest.current.getDraftIdentity();
                const controller = new AbortController();
                request.current?.abort();
                request.current = controller;
                const currentValue = latest.current.getCurrentValue();
                const safe = () =>
                    !controller.signal.aborted &&
                    version === requestVersion.current &&
                    useAuthStore.getState().currentUser === actor &&
                    currentAuthority() === authority &&
                    !latest.current.disabled &&
                    latest.current.getDraftIdentity() === identity;
                setMessages((previous) => [...previous, { role: "user", content: nextInstruction }]);
                setInstruction("");
                onPendingChange(true);
                mutate(
                    {
                        instruction: nextInstruction,
                        value: currentValue,
                        selected_api_names: Utils.Type.isArray(currentValue["api_names"]) ? (currentValue["api_names"] as string[]) : [],
                        selected_comfort_tool_names: Utils.Type.isArray(currentValue["comfort_tool_names"])
                            ? (currentValue["comfort_tool_names"] as string[])
                            : [],
                        signal: controller.signal,
                    },
                    {
                        onSuccess: (draft) => {
                            if (!safe()) return;
                            latest.current.onApplyDraft(draft);
                            setMessages((previous) => [
                                ...previous,
                                { role: "assistant", content: t("bot.agent.Bot draft applied", { name: draft.bot_name }) },
                            ]);
                            Toast.Add.success(t("successes.Bot draft applied."));
                        },
                        onError: () => {
                            if (safe())
                                setMessages((previous) => [...previous, { role: "assistant", content: t("bot.agent.Draft generation failed") }]);
                        },
                        onSettled: () => {
                            if (version === requestVersion.current) onPendingChange(false);
                        },
                    }
                );
            },
        });
    };

    return (
        <Box rounded border px="3" py="3" className="bg-muted/20">
            <Flex items="center" gap="2" mb="2">
                <Box className="rounded-full bg-primary/10 p-1.5 text-primary">
                    <IconComponent icon="bot" size="4" />
                </Box>
                <Box textSize="sm" weight="medium">
                    {t("bot.agent.Bot creation assistant")}
                </Box>
                <Badge variant="secondary" className="ml-auto px-1.5 py-0 text-[11px]">
                    {t("bot.agent.Draft only")}
                </Badge>
            </Flex>
            {messages.length ? (
                <Flex direction="col" gap="1.5" mb="2" className="max-h-40 overflow-y-auto">
                    {messages.map((message, index) => (
                        <Box
                            key={`bot-create-assistant-message-${index}`}
                            textSize="xs"
                            className={
                                message.role === "user"
                                    ? "ml-8 rounded-md bg-primary px-2 py-1.5 text-primary-foreground"
                                    : "mr-8 rounded-md border border-input bg-background px-2 py-1.5 text-muted-foreground"
                            }
                        >
                            {message.content}
                        </Box>
                    ))}
                </Flex>
            ) : null}
            <Textarea
                value={instruction}
                placeholder={t("bot.agent.Describe the bot to create")}
                rows={3}
                disabled={disabled || isPending}
                className="resize-none"
                onChange={(event) => setInstruction(event.currentTarget.value)}
            />
            <Flex justify="end" mt="2">
                <Button type="button" size="sm" className="gap-1.5" disabled={!canSend} onClick={submit}>
                    <IconComponent icon="send" size="3.5" />
                    {t("bot.agent.Create draft")}
                </Button>
            </Flex>
        </Box>
    );
}

export default BotCreateAssistantPanel;
