"""Server-owned official catalog. Download only reviewed GitHub release assets."""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import quote, urlsplit
import httpx
from langboard_shared.domain.services.AppPackage import MAX_PACKAGE_BYTES, validate_package


@dataclass(frozen=True)
class OfficialApp:
    key: str
    repository: str
    asset_name: str
    name: str
    description: str


OFFICIAL_APPS = (
    OfficialApp("github-panel", "yamonco/langboard-app-github", "langboard-app-github.zip", "GitHub", "Checks and commit statuses from your authorized signal inbox."),
    OfficialApp("glitchtip-panel", "yamonco/langboard-app-glitchtip", "langboard-app-glitchtip.zip", "GlitchTip", "Issue observations from your authorized signal inbox."),
    OfficialApp("dokploy-panel", "yamonco/langboard-app-dokploy", "langboard-app-dokploy.zip", "Dokploy", "Deployment statuses from your authorized signal inbox."),
)


def _url_allowed(url, *, binary=False):
    parsed = urlsplit(url)
    hosts = {"github.com", "release-assets.githubusercontent.com"} if binary else {"api.github.com"}
    if parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError("Untrusted official asset destination")


def _read(client, url, limit, *, binary=False):
    _url_allowed(url, binary=binary)
    headers = {"Accept": "application/octet-stream" if binary else "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    for _ in range(3):
        with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = response.headers.get("location", "")
                _url_allowed(url, binary=binary)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ValueError("Official release is unavailable") from exc
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("Official response exceeds limit")
            return bytes(body)
    raise ValueError("Official asset redirect limit exceeded")


def download_official_package(key: str, tag: str | None = None, *, client=None) -> tuple[bytes, dict]:
    item = next((item for item in OFFICIAL_APPS if item.key == key), None)
    if item is None or (tag is not None and not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag)):
        raise ValueError("Unknown official app or release")
    if client is None:
        with httpx.Client(timeout=20, trust_env=False) as session:
            try:
                return download_official_package(key, tag, client=session)
            except httpx.RequestError as exc:
                raise ValueError("Official release download failed") from exc
    selector = "latest" if tag is None else f"tags/{quote(tag, safe='')}"
    try:
        release = json.loads(_read(client, f"https://api.github.com/repos/{item.repository}/releases/{selector}", 1024 * 1024))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid official release response") from exc
    if not isinstance(release, dict):
        raise ValueError("Invalid official release response")
    tag = release.get("tag_name") if tag is None else tag
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ValueError("Stable official version required")
    if release.get("draft") or release.get("prerelease") or release.get("tag_name") != tag:
        raise ValueError("Stable official release required")
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list) or any(not isinstance(asset, dict) for asset in raw_assets):
        raise ValueError("Invalid official release assets")
    assets = [asset for asset in raw_assets if asset.get("name") == item.asset_name and asset.get("state") == "uploaded"]
    if len(assets) != 1:
        raise ValueError("Official package asset unavailable")
    asset = assets[0]
    digest = asset.get("digest", "")
    expected_url = f"https://github.com/{item.repository}/releases/download/{tag}/{item.asset_name}"
    if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or type(asset.get("id")) is not int or asset["id"] <= 0
        or asset.get("browser_download_url") != expected_url or type(asset.get("size")) is not int
        or not 0 < asset["size"] <= MAX_PACKAGE_BYTES):
        raise ValueError("Official asset identity unavailable")
    body = _read(client, expected_url, MAX_PACKAGE_BYTES, binary=True)
    if len(body) != asset["size"] or sha256(body).hexdigest() != digest.removeprefix("sha256:"):
        raise ValueError("Official asset digest mismatch")
    package = validate_package(body)
    if package["declaration"]["key"] != key or package["declaration"]["version"] != tag.removeprefix("v"):
        raise ValueError("Official package declaration mismatch")
    return body, {"kind": "official", "repository": item.repository, "tag": tag,
                  "asset_id": asset["id"], "sha256": package["sha256"]}
