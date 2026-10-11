import json
import stat
from hashlib import sha256
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import httpx
import pytest
from langboard.apps.OfficialAppPackages import download_official_package
from langboard_shared.domain.services.AppPackage import validate_package


DECLARATION = {"schema_version": 1, "key": "github-panel", "version": "1.0.0", "name": "Example",
               "description": "Independent panel", "capabilities": ["panels.render"], "resource_types": [],
               "panel": {"url": "https://panel.example/", "name": "Example", "icon": "app"}}


def bundle(extra=(), *, declaration=DECLARATION):
    body = BytesIO()
    with ZipFile(body, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({"package_version": 1, "declaration": declaration, "panel_entry": "panel/index.html"}))
        archive.writestr("panel/index.html", '<script type="module" src="./panel.mjs"></script>')
        archive.writestr("panel/panel.mjs", "export const ready = true;")
        archive.writestr("panel/host.mjs", "export const hostOrigin = 'https://placeholder.invalid';")
        for name, content in extra:
            archive.writestr(name, content)
    return body.getvalue()


def test_valid_zip_is_bounded_and_preserves_digest():
    body = bundle()
    result = validate_package(body)
    assert result["sha256"] == sha256(body).hexdigest()
    assert result["files"] == 4
    assert result["declaration"]["key"] == "github-panel"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "panel/../escape.js", "panel//duplicate.js", "panel/back\\slash.js", "server.py", "panel/.env", "panel/INDEX.HTML"])
def test_unsafe_zip_entries_are_rejected(name):
    with pytest.raises(ValueError):
        validate_package(bundle([(name, "unsafe")]))


def test_symlink_and_compression_bomb_rejected():
    link = ZipInfo("panel/link.js")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    for body in [bundle([(link, "index.html")]), bundle([("panel/bomb.txt", "x" * 200000)])]:
        with pytest.raises(ValueError):
            validate_package(body)


def test_official_download_checks_pinned_repository_release_digest_and_manifest():
    body = bundle()
    calls = []
    asset_url = "https://github.com/yamonco/langboard-app-github/releases/download/v1.0.0/langboard-app-github.zip"
    release = {"tag_name": "v1.0.0", "draft": False, "prerelease": False, "assets": [{"id": 42,
               "name": "langboard-app-github.zip", "state": "uploaded", "size": len(body),
               "digest": "sha256:" + sha256(body).hexdigest(), "browser_download_url": asset_url}]}

    def request(request):
        calls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=release)
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        downloaded, provenance = download_official_package("github-panel", client=client)
        assert downloaded == body and provenance["kind"] == "official"
        assert calls == ["https://api.github.com/repos/yamonco/langboard-app-github/releases/latest", asset_url]
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError, match="digest mismatch"):
            download_official_package("github-panel", "v1.0.0", client=client)
        release["assets"][0]["browser_download_url"] = "https://localhost/private"
        before = len(calls)
        with pytest.raises(ValueError, match="identity"):
            download_official_package("github-panel", "v1.0.0", client=client)
        assert len(calls) == before + 1


def test_official_redirect_never_reaches_private_host():
    body = bundle()
    calls = []
    def request(request):
        calls.append(str(request.url))
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"tag_name": "v1.0.0", "assets": [{"id": 42,
                "name": "langboard-app-github.zip", "state": "uploaded", "size": len(body),
                "digest": "sha256:" + sha256(body).hexdigest(),
                "browser_download_url": "https://github.com/yamonco/langboard-app-github/releases/download/v1.0.0/langboard-app-github.zip"}]})
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        with pytest.raises(ValueError, match="Untrusted"):
            download_official_package("github-panel", client=client)
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [b"not JSON", b"\xff", b"null", b"[]", b'"release"',
    b'{"tag_name":"v1.0.0","assets":null}', b'{"tag_name":"v1.0.0","assets":{}}',
    b'{"tag_name":"v1.0.0","assets":[null]}'])
def test_malformed_official_release_fails_before_asset_download(payload):
    calls = []
    def request(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=payload)
    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        with pytest.raises(ValueError, match="Invalid official release"):
            download_official_package("github-panel", client=client)
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("id", None), ("id", True), ("id", 0), ("id", "42"),
    ("digest", None), ("digest", 42), ("size", True), ("size", None)])
def test_malformed_official_asset_identity_fails_before_download(field, value):
    body = bundle()
    asset = {"id": 42, "name": "langboard-app-github.zip", "state": "uploaded", "size": len(body),
        "digest": "sha256:" + sha256(body).hexdigest(),
        "browser_download_url": "https://github.com/yamonco/langboard-app-github/releases/download/v1.0.0/langboard-app-github.zip"}
    if value is None:
        asset.pop(field)
    else:
        asset[field] = value
    calls = []
    def request(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"tag_name": "v1.0.0", "assets": [asset]})
    with httpx.Client(transport=httpx.MockTransport(request)) as client:
        with pytest.raises(ValueError, match="identity"):
            download_official_package("github-panel", client=client)
    assert len(calls) == 1


def test_store_icons_must_be_packaged_and_version_must_not_be_boolean():
    body = bundle()
    with ZipFile(BytesIO(body)) as source:
        manifest = json.loads(source.read("manifest.json"))
    for metadata in [{"icon": "https://remote.example/icon.svg"}, {"icon": "panel/missing.svg"}, {"screenshots": ["panel/index.html"]}]:
        output = BytesIO()
        with ZipFile(output, "w") as target:
            manifest["store"] = metadata
            target.writestr("manifest.json", json.dumps(manifest))
            target.writestr("panel/index.html", "Panel")
        with pytest.raises(ValueError):
            validate_package(output.getvalue())


def test_package_migration_keeps_registry_and_refuses_installed_downgrade():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine("sqlite://")
    path = Path(__file__).resolve().parents[2] / "langboard/migrations/versions/20261011210000-a75437bc41d2.py"
    spec = importlib.util.spec_from_file_location("package_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE app_definition (id INTEGER PRIMARY KEY, declaration JSON)"))
        connection.execute(text("INSERT INTO app_definition (id, declaration) VALUES (1, '{}')"))
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert "package" in {column["name"] for column in inspect(connection).get_columns("app_definition")}
        assert connection.execute(text("SELECT package FROM app_definition WHERE id=1")).scalar() is None
        connection.execute(text("UPDATE app_definition SET package='{}' WHERE id=1"))
        with pytest.raises(RuntimeError, match="Cannot discard"):
            module.downgrade()
    engine.dispose()
