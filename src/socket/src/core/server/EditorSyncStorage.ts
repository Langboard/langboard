import { DATA_DIR } from "@/Constants";
import crypto from "crypto";
import fs from "fs/promises";
import path from "path";

const EDITOR_SYNC_STORAGE_DIR = path.join(DATA_DIR, "editor-sync");

const getDocumentStatePath = (documentName: string) => {
    const documentHash = crypto.createHash("sha256").update(documentName).digest("hex");
    return path.join(EDITOR_SYNC_STORAGE_DIR, `${documentHash}.ydoc`);
};

const EditorSyncStorage = {
    async load(documentName: string): Promise<Uint8Array | null> {
        try {
            const state = await fs.readFile(getDocumentStatePath(documentName));
            return Uint8Array.from(state);
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code === "ENOENT") {
                return null;
            }

            throw error;
        }
    },

    async save(documentName: string, state: Uint8Array): Promise<void> {
        await fs.mkdir(EDITOR_SYNC_STORAGE_DIR, { recursive: true });

        const statePath = getDocumentStatePath(documentName);
        const temporaryStatePath = `${statePath}.${process.pid}.${Date.now()}.tmp`;

        await fs.writeFile(temporaryStatePath, state);
        await fs.rename(temporaryStatePath, statePath);
    },

    async delete(documentName: string): Promise<void> {
        try {
            await fs.unlink(getDocumentStatePath(documentName));
        } catch (error) {
            if ((error as NodeJS.ErrnoException).code === "ENOENT") {
                return;
            }

            throw error;
        }
    },
};

export default EditorSyncStorage;
