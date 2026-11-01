from json import dumps as json_dumps
from langboard_shared.Env import Env


def rich_patch_request_fits_limit(document_name: str, value: str, restore_snapshot: str | None = None) -> bool:
    data = {"document_name": document_name, "value": value}
    if restore_snapshot is not None:
        data["restore_snapshot"] = restore_snapshot
    payload = json_dumps(data, ensure_ascii=False, separators=(",", ":"))
    return len(payload.encode("utf-8")) <= Env.EDITOR_SYNC_MAX_REQUEST_SIZE_MB * 1024 * 1024
