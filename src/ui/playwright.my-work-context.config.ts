import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const python = process.env.LANGBOARD_TEST_PYTHON || "uv run --no-sync python";

export default defineConfig({
    testDir: "./src/pages/DashboardPage/my-work-context",
    testMatch: "MyWork.spec.ts",
    workers: 1,
    retries: 0,
    timeout: 60_000,
    reporter: "line",
    use: {
        baseURL: "http://127.0.0.1:4192",
        channel: "chrome",
        headless: true,
        timezoneId: "Asia/Seoul",
        viewport: { width: 1440, height: 900 },
        screenshot: "only-on-failure",
    },
    webServer: [
        {
            cwd: root,
            command: `${python} -B src/api/tests/fixtures/my_work_workspace_server.py`,
            env: {
                PROJECT_NAME: "langboard",
                PYTHONPATH: "src/api:src/shared/py",
                PYTHONDONTWRITEBYTECODE: "1",
                PYTHON_DOTENV_DISABLED: "1",
                MAIN_DATABASE_URL: "sqlite://",
                READONLY_DATABASE_URL: "sqlite://",
            },
            url: "http://127.0.0.1:5392/fixture",
            reuseExistingServer: false,
            gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
            timeout: 60_000,
        },
        {
            command: "node_modules/.bin/vite --config vite.card-load.config.ts --host 127.0.0.1 --port 4192 --strictPort",
            env: { API_PORT: "5392", SOCKET_PORT: "5392" },
            url: "http://127.0.0.1:4192/src/pages/DashboardPage/my-work-context/MyWork.fixture.html",
            reuseExistingServer: false,
            timeout: 60_000,
        },
    ],
});
