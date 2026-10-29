import process from "node:process";
import http from "node:http";
import { createGunzip } from "node:zlib";
import { URL } from "node:url";

// Forward fixture traffic through the real owner; a generic Yjs server cannot
// exercise rich restoration admission, persistence acknowledgement, or CAS.
if (!process.env.COLLABORATIVE_PHOENIX_ORIGIN) {
    const { runCollaborativeOwner } = await import("./run-collaborative-owner.mjs");
    await runCollaborativeOwner();
} else {
    const origin = new URL(process.env.COLLABORATIVE_PHOENIX_ORIGIN);
    if (origin.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(origin.hostname) || origin.port === "4243") {
        throw new Error("The collaborative fixture requires a separate loopback Phoenix owner.");
    }
    const allowedOrigins = new Set(["http://127.0.0.1:4242", "http://localhost:4242", "http://127.0.0.1:4183", "http://localhost:4183"]);
    const sockets = new Set();
    const headersFor = (request) => ({ ...request.headers, host: origin.host });
    const upgradeResponse = (response) => {
        const headers = response.rawHeaders.reduce((lines, value, index) => lines + (index % 2 ? `${value}\r\n` : `${value}: `), "");
        return `HTTP/1.1 ${response.statusCode} ${response.statusMessage}\r\n${headers}\r\n`;
    };
    const server = http.createServer((request, response) => {
        const requestOrigin = request.headers.origin;
        if (requestOrigin && !allowedOrigins.has(requestOrigin)) {
            response.writeHead(403).end();
            return;
        }
        if (requestOrigin) response.setHeader("Access-Control-Allow-Origin", requestOrigin);
        response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type, Content-Encoding, X-Api-Token, X-Editor-Route-Key");
        response.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
        response.setHeader("Access-Control-Allow-Credentials", "true");
        if (request.method === "OPTIONS") {
            response.writeHead(204).end();
            return;
        }
        const headers = headersFor(request);
        let body = request;
        if (headers["content-encoding"] === "gzip") {
            delete headers["content-encoding"];
            delete headers["content-length"];
            body = request.pipe(createGunzip());
            body.on("error", () => response.writeHead(400).end());
        }
        const upstream = http.request(new URL(request.url, origin), { method: request.method, headers }, (result) => {
            response.writeHead(result.statusCode, result.headers);
            result.pipe(response);
        });
        upstream.on("error", () => response.writeHead(503).end(JSON.stringify({ message: "The test Phoenix owner is unavailable." })));
        body.pipe(upstream);
    });

    server.on("connection", (socket) => {
        sockets.add(socket);
        socket.on("close", () => sockets.delete(socket));
    });
    server.on("upgrade", (request, socket, head) => {
        const target = new URL(request.url, origin);
        target.pathname = "/editor-sync";
        const upstream = http.request(target, { headers: headersFor(request) });
        upstream.on("upgrade", (result, remote, remoteHead) => {
            socket.write(upgradeResponse(result));
            if (remoteHead.length) socket.write(remoteHead);
            if (head.length) remote.write(head);
            remote.on("error", () => socket.destroy());
            socket.on("error", () => remote.destroy());
            remote.pipe(socket).pipe(remote);
        });
        upstream.on("response", (result) => {
            socket.write(upgradeResponse(result));
            result.pipe(socket);
        });
        upstream.on("error", () => socket.destroy());
        upstream.end();
    });
    await new Promise((resolve) => server.listen(4243, "127.0.0.1", resolve));
    process.stdout.write(`Collaborative fixture proxy → ${origin.origin} (native Phoenix owner; fixture identity).\n`);
    for (const signal of ["SIGTERM", "SIGINT"])
        process.on(signal, () => {
            for (const socket of sockets) socket.destroy();
            server.close(() => process.exit(0));
        });
}
