"""Verify the declared Python SDK against reviewed provenance before installation."""

import hashlib
import json
import re
import tomllib
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile


def verify(root: Path) -> Path:
    project = tomllib.loads((root / "pyproject.toml").read_text())
    declared = project["tool"]["uv"]["sources"]["langboard-sdk"]["path"]
    artifact = (root / declared).resolve()
    vendor = (root / "src/sdk/vendor").resolve()
    if artifact.parent != vendor or not re.fullmatch(r"langboard_sdk-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl", artifact.name):
        raise ValueError("Invalid SDK artifact path")
    provenance = json.loads((vendor / "provenance.json").read_text())
    expected = provenance["artifacts"].get(artifact.name)
    if not isinstance(expected, str) or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
        raise ValueError("SDK digest mismatch")
    version = provenance["packages"]["langboard-sdk"]
    with ZipFile(artifact) as wheel:
        metadata = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Invalid SDK metadata")
        package = BytesParser().parsebytes(wheel.read(metadata[0]))
    if package["Name"] != "langboard-sdk" or package["Version"] != version:
        raise ValueError("SDK package identity mismatch")
    for project_root in (root, root / "src/shared/py", root / "src/graph"):
        config = tomllib.loads((project_root / "pyproject.toml").read_text())
        source = config["tool"]["uv"]["sources"]["langboard-sdk"]["path"]
        if (project_root / source).resolve() != artifact:
            raise ValueError("SDK project artifact mismatch")
        lock = tomllib.loads((project_root / "uv.lock").read_text())
        entries = [entry for entry in lock["package"] if entry["name"] == "langboard-sdk"]
        lock_path = entries[0]["source"].get("path") if len(entries) == 1 else None
        if (
            len(entries) != 1
            or entries[0]["version"] != version
            or not isinstance(lock_path, str)
            or (project_root / lock_path).resolve() != artifact
        ):
            raise ValueError("SDK lock identity mismatch")
        if entries[0]["wheels"] != [{"filename": artifact.name, "hash": "sha256:" + expected}]:
            raise ValueError("SDK lock digest mismatch")
    return artifact


if __name__ == "__main__":
    print(verify(Path(__file__).resolve().parents[2]))
