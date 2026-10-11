"""Bounded ZIP package validation; never extracts or executes server code."""

import json
import re
import stat
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
from langboard_sdk.definition import validate_app_definition


MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 24 * 1024 * 1024
MAX_PACKAGE_FILES = 256
STATIC_EXTENSIONS = {".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".woff", ".woff2", ".txt", ".ico"}


def validate_package(body: bytes) -> dict:
    if not isinstance(body, bytes) or not body or len(body) > MAX_PACKAGE_BYTES:
        raise ValueError("Invalid package size")
    try:
        with ZipFile(BytesIO(body)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_PACKAGE_FILES:
                raise ValueError("Invalid package entry count")
            files, total = {}, 0
            for entry in entries:
                name = entry.filename
                path = PurePosixPath(name)
                if (entry.orig_filename != name or not re.fullmatch(r"[A-Za-z0-9_./-]{1,240}", name)
                    or name.startswith("/") or "\\" in name or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
                    or str(path) != name.rstrip("/") or name.casefold() in files):
                    raise ValueError("Invalid or duplicate package path")
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1:
                    raise ValueError("Unsupported package entry")
                if entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                    raise ValueError("Unsupported package compression")
                files[name.casefold()] = entry
                total += entry.file_size
                if entry.file_size > MAX_PACKAGE_BYTES or total > MAX_EXPANDED_BYTES or entry.file_size > max(1024, entry.compress_size * 200):
                    raise ValueError("Expanded package limit exceeded")
                if not entry.is_dir() and name != "manifest.json" and (not name.startswith("panel/") or path.suffix.lower() not in STATIC_EXTENSIONS):
                    raise ValueError("Only manifest and static panel assets are supported")
            manifest = files.get("manifest.json")
            if manifest is None or manifest.filename != "manifest.json" or manifest.file_size > 65536:
                raise ValueError("Package manifest missing or oversized")
            package = json.loads(archive.read(manifest))
            if not isinstance(package, dict) or (set(package) - {"package_version", "declaration", "panel_entry", "store"} or not {"package_version", "declaration", "panel_entry"}.issubset(package)) or type(package["package_version"]) is not int or package["package_version"] != 1:
                raise ValueError("Unsupported package manifest")
            declaration = validate_app_definition(package["declaration"])
            entry = package["panel_entry"]
            if not isinstance(entry, str) or not entry.startswith("panel/") or not entry.endswith(".html") or entry.casefold() not in files or files[entry.casefold()].filename != entry or files[entry.casefold()].is_dir():
                raise ValueError("Panel entry missing")
            if "panels.render" not in declaration["capabilities"] or not declaration.get("panel"):
                raise ValueError("Panel package requires a panel declaration")
            store = package.get("store", {})
            if not isinstance(store, dict) or set(store) - {"icon", "icon_dark", "screenshots"}:
                raise ValueError("Invalid package store metadata")
            images = store.get("screenshots", [])
            if not isinstance(images, list) or len(images) > 5:
                raise ValueError("Invalid package screenshots")
            for image in [store[key] for key in ("icon", "icon_dark") if key in store] + images:
                if (not isinstance(image, str) or not image.startswith("panel/")
                    or PurePosixPath(image).suffix.lower() not in {".svg", ".png", ".jpg", ".jpeg", ".webp"}
                    or image.casefold() not in files or files[image.casefold()].filename != image
                    or files[image.casefold()].is_dir()):
                    raise ValueError("Package store asset missing")
            # Force CRC verification before accepting the complete archive.
            for item in entries:
                if not item.is_dir():
                    with archive.open(item) as source:
                        if len(source.read(item.file_size + 1)) != item.file_size:
                            raise ValueError("Invalid expanded file size")
            return {"declaration": declaration, "panel_entry": entry, "sha256": sha256(body).hexdigest(),
                    "size": len(body), "expanded_size": total, "files": len(entries), "store": store}
    except (BadZipFile, UnicodeError, json.JSONDecodeError, KeyError, RuntimeError) as exc:
        raise ValueError("Invalid app package") from exc
