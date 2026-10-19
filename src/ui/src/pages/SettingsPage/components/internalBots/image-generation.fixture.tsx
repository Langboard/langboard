import { useState } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthUser, InternalBotModel } from "@/core/models";
import useAuthStore from "@/core/stores/AuthStore";
import ImageGenerationSettings from "./ImageGenerationSettings";
import InternalBotCreateFormDialog from "./InternalBotCreateFormDialog";
import { defaultImageGenerationConfig } from "./ImageGenerationFields";
import "@/i18n";
import "@/assets/styles/main.css";
const date = new Date("2026-10-19T00:00:00Z");
const userData = {
    uid: "image-admin",
    type: "user",
    username: "admin",
    firstname: "Image",
    lastname: "Admin",
    is_admin: true,
    setting_role_actions: ["*"],
    preferred_lang: "en-US",
    created_at: date,
    updated_at: date,
};
const actor = AuthUser.Model.fromOne(userData);
useAuthStore.setState({ currentUser: actor, state: "loaded" });
const config = { ...defaultImageGenerationConfig(), enabled: true, model_name: "image-model" };
const bot = InternalBotModel.Model.fromOne({
    uid: "image-profile",
    display_name: "Image profile",
    bot_type: "image_generation",
    platform: "default",
    platform_running_type: "default",
    api_url: "",
    api_key: "server-key-must-not-return",
    api_key_configured: true,
    value: JSON.stringify(config),
    is_default: false,
    created_at: date,
    updated_at: date,
});
const client = new QueryClient();
function Fixture() {
    const [canUpdate, setCanUpdate] = useState(true);
    const [opened, setOpened] = useState(false);
    const currentUser = useAuthStore((state) => state.currentUser)!;
    return (
        <main className="mx-auto max-w-3xl p-3">
            <nav className="mb-3 flex flex-wrap gap-3">
                <button onClick={() => setOpened(true)}>Open creation</button>
                <button onClick={() => setCanUpdate((value) => !value)}>Toggle permission</button>
                <button onClick={() => useAuthStore.setState({ currentUser: AuthUser.Model.fromOne({ ...userData, uid: "other-admin" }) })}>
                    Switch actor
                </button>
                <button
                    onClick={() =>
                        InternalBotModel.Model.fromOne(
                            {
                                uid: bot.uid,
                                value: JSON.stringify({ ...config, model_name: "rotated-model" }),
                                created_at: date,
                                updated_at: new Date("2026-10-19T01:00:00Z"),
                            },
                            true
                        )
                    }
                >
                    Rotate model
                </button>
                <button
                    onClick={() =>
                        InternalBotModel.Model.fromOne(
                            {
                                uid: bot.uid,
                                value: JSON.stringify({ ...config, enabled: false }),
                                created_at: date,
                                updated_at: new Date("2026-10-19T02:00:00Z"),
                            },
                            true
                        )
                    }
                >
                    Disable profile
                </button>
            </nav>
            <ImageGenerationSettings bot={bot} canUpdate={canUpdate} />
            <InternalBotCreateFormDialog opened={opened} setOpened={setOpened} currentUser={currentUser} />
            <output data-testid="avatar">{bot.avatar ?? "Original avatar unchanged"}</output>
        </main>
    );
}
createRoot(document.getElementById("root")!).render(
    <QueryClientProvider client={client}>
        <MemoryRouter>
            <Fixture />
        </MemoryRouter>
    </QueryClientProvider>
);
