import process from "node:process";
import { Server } from "@hocuspocus/server";
const server = new Server({ port: 4243, address: "127.0.0.1", quiet: true });
await server.listen();
process.on("SIGTERM", async () => {
    await server.destroy();
    process.exit(0);
});
