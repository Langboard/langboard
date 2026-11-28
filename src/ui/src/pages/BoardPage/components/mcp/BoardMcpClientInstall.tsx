import Button from "@/components/base/Button";
import Input from "@/components/base/Input";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const clients = ["Codex", "Claude Code", "Claude", "Cursor", "VS Code", "Hermes", "OpenClaw"] as const;
type Client = (typeof clients)[number];
const shell = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;

export default function BoardMcpClientInstall({ name, url, resource }: { name: string; url: string; resource: string | null }) {
    const [t] = useTranslation();
    const [search, setSearch] = useState("");
    const [client, setClient] = useState<Client | null>(null);
    const [copyStatusKey, setCopyStatusKey] = useState("");
    const searchInput = useRef<HTMLInputElement>(null);
    const backButton = useRef<HTMLButtonElement>(null);
    const manual = JSON.stringify({ mcpServers: { [name]: { url } } }, null, 2);
    const prompt = t("mcp.Install prompt", { name, url });
    const snippets: Record<Client, string> = {
        Codex: `codex mcp add ${shell(name)} --url ${shell(url)}\ncodex mcp login ${shell(name)}`,
        "Claude Code": `claude mcp add --transport http ${shell(name)} ${shell(url)}`,
        Claude: url,
        Cursor: manual,
        "VS Code": JSON.stringify({ servers: { [name]: { type: "http", url } } }, null, 2),
        Hermes: `mcp_servers:\n  ${JSON.stringify(name)}:\n    url: ${JSON.stringify(url)}\n    auth: oauth`,
        OpenClaw: prompt,
    };
    const cursorConfig = btoa(Array.from(new TextEncoder().encode(JSON.stringify({ url })), (byte) => String.fromCharCode(byte)).join(""));
    const installLink =
        client === "Cursor"
            ? `cursor://anysphere.cursor-deeplink/mcp/install?name=${encodeURIComponent(name)}&config=${encodeURIComponent(cursorConfig)}`
            : client === "VS Code"
              ? `vscode:mcp/install?${encodeURIComponent(JSON.stringify({ name, type: "http", url }))}`
              : null;
    const copy = async (value: string) => {
        try {
            await navigator.clipboard.writeText(value);
            setCopyStatusKey("mcp.Copied configuration");
        } catch {
            setCopyStatusKey("mcp.Copy failed");
        }
    };
    const code = (value: string) => (
        <div className="space-y-2">
            <pre className="max-w-full overflow-x-auto rounded-md bg-muted p-2 text-xs" tabIndex={0}>
                <code>{value}</code>
            </pre>
            <Button size="sm" variant="outline" onClick={() => void copy(value)}>
                {t("mcp.Copy config")}
            </Button>
        </div>
    );
    return (
        <section className="min-w-0 space-y-3 rounded-md border p-3" aria-label={t("mcp.Install client")}>
            <h3 className="text-sm font-semibold">{t("mcp.Install client")}</h3>
            <p className="text-xs">{t("mcp.Manual setup help")}</p>
            <dl className="space-y-1 text-xs">
                <dt>{t("mcp.Server name")}</dt>
                <dd className="break-all">
                    <code>{name}</code>
                </dd>
                <dt>{t("mcp.Server URL")}</dt>
                <dd className="break-all">
                    <code>{url}</code>
                </dd>
            </dl>
            {client ? (
                <>
                    <Button
                        ref={backButton}
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                            setClient(null);
                            setCopyStatusKey("");
                            requestAnimationFrame(() => searchInput.current?.focus());
                        }}
                    >
                        {t("mcp.All clients")}
                    </Button>
                    <h4 className="text-sm font-semibold">{client}</h4>
                    <p className="text-xs">{t(`mcp.Client ${client}`)}</p>
                    {installLink && (
                        <Button size="sm" variant="outline" asChild>
                            <a href={installLink}>{t("mcp.One click install")}</a>
                        </Button>
                    )}
                    {code(snippets[client])}
                    {client === "Hermes" && (
                        <>
                            <p className="text-xs">{t("mcp.Configuration file", { path: "~/.hermes/config.yaml" })}</p>
                            <Button size="sm" variant="outline" onClick={() => void copy(prompt)}>
                                {t("mcp.Copy install prompt")}
                            </Button>
                        </>
                    )}
                    <p className="text-xs">{t("mcp.Complete authentication")}</p>
                </>
            ) : (
                <>
                    <Input
                        ref={searchInput}
                        aria-label={t("mcp.Search clients")}
                        placeholder={t("mcp.Search clients")}
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                    />
                    <ul className="space-y-2">
                        {clients
                            .filter((item) => item.toLowerCase().includes(search.trim().toLowerCase()))
                            .map((item) => (
                                <li key={item}>
                                    <Button
                                        className="h-auto w-full justify-start whitespace-normal text-left"
                                        variant="outline"
                                        onClick={() => {
                                            setClient(item);
                                            setCopyStatusKey("");
                                            requestAnimationFrame(() => backButton.current?.focus());
                                        }}
                                    >
                                        <span aria-hidden="true" className="mr-2 rounded border px-1 font-mono">
                                            {item.slice(0, 1)}
                                        </span>
                                        <span>
                                            <span className="block font-medium">{item}</span>
                                            <span className="block text-xs font-normal">{t(`mcp.Client ${item}`)}</span>
                                        </span>
                                    </Button>
                                </li>
                            ))}
                    </ul>
                    {!clients.some((item) => item.toLowerCase().includes(search.trim().toLowerCase())) && <p role="status">{t("mcp.No clients")}</p>}
                </>
            )}
            <details onToggle={() => setCopyStatusKey("")}>
                <summary className="cursor-pointer text-sm">{t("mcp.Advanced")}</summary>
                <div className="mt-2 space-y-2">
                    <p className="text-xs">{t("mcp.Advanced help")}</p>
                    {resource && (
                        <p className="break-all text-xs">
                            {t("mcp.OAuth resource")}: <code>{resource}</code>
                        </p>
                    )}
                    {code(manual)}
                    {code(snippets["Claude Code"])}
                </div>
            </details>
            {copyStatusKey && (
                <p role="status" className="text-xs">
                    {t(copyStatusKey)}
                </p>
            )}
            <p className="text-xs text-muted-foreground">{t("mcp.Install verification help")}</p>
        </section>
    );
}
