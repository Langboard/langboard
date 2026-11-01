import { createRoot } from "react-dom/client";
import { useEffect, useState } from "react";
import { IGraphExecutionPlan } from "@/core/models/GraphApprovalRequestModel";
import ToolArgumentEdit from "./ToolArgumentEdit";
import i18n from "@/i18n";
import "@/assets/styles/main.css";

void i18n.changeLanguage("en-US");
type Snapshot = { approval_uid: string; project_uid: string; plan: IGraphExecutionPlan; writes: unknown[]; status: string };
function Fixture() {
    const [snapshot, setSnapshot] = useState<Snapshot>();
    const [pending, setPending] = useState(false);
    const [error, setError] = useState("");
    const [submissions, setSubmissions] = useState<unknown[]>([]);
    useEffect(() => {
        void fetch("http://127.0.0.1:56949/state")
            .then((r) => r.json())
            .then(setSnapshot);
    }, []);
    const resume = async (decision: unknown) => {
        if (!snapshot) return;
        setPending(true);
        setSubmissions((all) => [...all, decision]);
        const response = await fetch("http://127.0.0.1:56949/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_uid: snapshot.project_uid, resume: decision }),
        });
        if (response.ok) setSnapshot(await response.json());
        else setError(await response.text());
        setPending(false);
    };
    return (
        <main className="mx-auto max-w-2xl p-6">
            <h1 className="text-xl font-semibold">Native HITL argument edit</h1>
            <p>Native SQLite claim and LangGraph interrupt fixture. Model and external write mocked.</p>
            {snapshot && (
                <>
                    <p data-testid="status">{snapshot.status}</p>
                    <pre data-testid="plan">{JSON.stringify(snapshot.plan, null, 2)}</pre>
                    {snapshot.status === "pending" && (
                        <>
                            <ToolArgumentEdit
                                key={snapshot.plan.plan_id}
                                plan={snapshot.plan}
                                disabled={pending}
                                onSubmit={(edit) => void resume({ approved: false, rejected: false, edit })}
                            />
                            <button className="btn mt-4" disabled={pending} onClick={() => void resume({ approved: true, rejected: false })}>
                                Approve separately
                            </button>
                        </>
                    )}
                    <pre data-testid="writes">{JSON.stringify(snapshot.writes)}</pre>
                </>
            )}
            <pre data-testid="submissions">{JSON.stringify(submissions)}</pre>
            {error && <p role="alert">{error}</p>}
        </main>
    );
}
createRoot(document.getElementById("root")!).render(<Fixture />);
