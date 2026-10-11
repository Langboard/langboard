import { createRequire } from "node:module";
import process from "node:process";
import { URL } from "node:url";
const require = createRequire(new URL("../../socket/package.json", import.meta.url));
const { Server } = require("@hocuspocus/server");
const server = new Server({ port: 4243, address: "127.0.0.1", quiet: true });
await server.listen();
process.on("SIGTERM", async () => {
    await server.destroy();
    process.exit(0);
});
