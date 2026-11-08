export interface InstalledSdkMetadata {
    name: string;
    version: string;
    module_sha256: string;
}
export function resolvePanelModuleEntry(uiRoot: string): string;
export function readPanelHostMetadata(uiRoot: string): InstalledSdkMetadata;
export function resolveHostRuntimeModuleEntry(uiRoot: string): string;
export function readHostRuntimeMetadata(uiRoot: string): InstalledSdkMetadata;
