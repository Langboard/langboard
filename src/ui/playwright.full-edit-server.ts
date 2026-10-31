export const fullEditServer = {
    command: "node_modules/.bin/vite --config vite.full-edit-save.config.ts --host 127.0.0.1 --port 4297 --strictPort",
    url: "http://127.0.0.1:4297/__fixture_ready__",
    reuseExistingServer: false,
    stdout: "pipe" as const,
    timeout: 60000,
};
