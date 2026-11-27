import secrets
from pathlib import Path
from .VaultProvider import VaultProvider


class LocalDevVaultProvider(VaultProvider):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def name(self) -> str:
        return "local-dev"

    def create_key(self, key_id: str) -> str:
        key_material = secrets.token_urlsafe(32)
        self._get_key_path(key_id).write_text(key_material, encoding="utf-8")
        return key_material

    def get_key(self, key_id: str) -> str:
        key_path = self._get_key_path(key_id)
        if not key_path.exists():
            raise KeyError(f"API key '{key_id}' not found in local dev vault")
        return key_path.read_text(encoding="utf-8")

    def delete_key(self, key_id: str):
        key_path = self._get_key_path(key_id)
        if key_path.exists():
            key_path.unlink()

    def health_check(self) -> bool:
        return self.base_dir.exists() and self.base_dir.is_dir()

    def _get_key_path(self, key_id: str) -> Path:
        return self.base_dir / key_id
