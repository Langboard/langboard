"""Optional AWS SDK must not prevent local file operations in a fresh process."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest
from .AttachmentStorageOperations_test import registry  # noqa: F401


PROBE = """
import importlib.abc
import sys
from io import BytesIO
from pathlib import Path
class BlockAws(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'boto3', 'botocore', 's3transfer'}:
            raise ModuleNotFoundError('Optional AWS SDK absent: ' + name)
sys.meta_path.insert(0, BlockAws())
from langboard_shared.Env import Env
type(Env).LOCAL_STORAGE_DIR = property(lambda _: Path(sys.argv[1]))
from langboard_shared.core.storage import Storage, StorageName, FileModel
from langboard_shared.core.storage.BaseStorage import BaseStorage
from sqlalchemy import create_engine
from langboard_shared.core.db.DbEngine import DbEngine
from langboard_shared.domain.models import AttachmentStorageObject, AttachmentStorageReference
engine = create_engine('sqlite:///' + str(Path(sys.argv[1]) / 'registry.db'))
DbEngine.get_main_engine = lambda: engine
for model in (AttachmentStorageObject, AttachmentStorageReference):
    model.__table__.create(engine)
file = Storage.upload_named(BytesIO(b'local payload'), StorageName.CardAttachment, 'probe.txt', 'source.txt')
assert file is not None and file.storage_type == 'local'
assert Storage.get_file(file) == b'local payload'
encrypted = file.path.split('/')[2]
assert Storage.get(encrypted, file.storage_name, file.filename) == b'local payload'
destination = BytesIO()
assert Storage.download_file(file, destination) and destination.getvalue() == b'local payload'
destination = BytesIO()
assert Storage.download(encrypted, file.storage_name, file.filename, destination)
assert destination.getvalue() == b'local payload'
# Missing S3 support must not read or remove an identically named local object.
foreign = FileModel(**{**file.model_dump(), 'storage_type': 's3'})
assert Storage.get_file(foreign) is None
assert not Storage.download_file(foreign, BytesIO())
assert not Storage.delete(foreign)
assert Storage.get_file(file) == b'local payload'
assert Storage.delete(file) and Storage.get_file(file) is None
assert not any(name.split('.')[0] in {'boto3','botocore','s3transfer'} for name in sys.modules)
print('LOCAL_STORAGE_WITHOUT_AWS_VERIFIED')
"""


def test_local_storage_without_aws_sdk(tmp_path):
    root = Path(__file__).resolve().parents[6]
    env = {
        **os.environ,
        "PROJECT_NAME": "langboard",
        "S3_ACCESS_KEY_ID": "fixture-only",
        "S3_SECRET_ACCESS_KEY": "fixture-only",
        "COMMON_SECRET_KEY": "fixture-only-local-storage",
        "PYTHONPATH": str(root / "src/shared/py") + os.pathsep + str(root / "src/api"),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(PROBE), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "LOCAL_STORAGE_WITHOUT_AWS_VERIFIED" in result.stdout


@pytest.mark.usefixtures("registry")
def test_s3_selection_keeps_object_keys_cleanup_and_failure_contract(monkeypatch):
    """Exercise the existing S3 adapter without reaching a cloud endpoint."""
    import sys
    from io import BytesIO
    from types import ModuleType
    from unittest.mock import Mock
    from langboard_shared.core.storage import Storage, StorageName
    from langboard_shared.core.storage.LocalStorage import LocalStorage
    from langboard_shared.core.storage.S3Storage import S3Storage
    from langboard_shared.Env import Env

    monkeypatch.setenv("COMMON_SECRET_KEY", "fixture-only-local-storage")
    monkeypatch.setattr(type(Env), "S3_ACCESS_KEY_ID", property(lambda _: "fixture-only"))
    monkeypatch.setattr(type(Env), "S3_SECRET_ACCESS_KEY", property(lambda _: "fixture-only"))
    monkeypatch.setattr(Storage, "_storages", {"s3": S3Storage(), "local": LocalStorage()})
    try:
        from botocore.exceptions import ClientError
    except ModuleNotFoundError:
        exceptions = ModuleType("botocore.exceptions")

        class ClientError(Exception):
            pass

        exceptions.ClientError = ClientError
        monkeypatch.setitem(sys.modules, "botocore.exceptions", exceptions)
    client = Mock()
    objects = {}

    def upload(*, Fileobj, Bucket, Key):
        objects[(Bucket, Key)] = Fileobj.read()

    def download(*, Bucket, Key, Fileobj):
        Fileobj.write(objects[(Bucket, Key)])

    client.meta.service_model.operation_model.return_value.input_shape.members = {"IfNoneMatch": object()}
    client.put_object.side_effect = lambda *, Bucket, Key, Body, IfNoneMatch: objects.setdefault((Bucket, Key), bytes(Body))
    client.upload_fileobj.side_effect = upload
    client.download_fileobj.side_effect = download
    client.delete_object.side_effect = lambda *, Bucket, Key: objects.pop((Bucket, Key), None)
    monkeypatch.setattr(S3Storage, "_connect_client", lambda _: client)
    file = Storage.upload_named(BytesIO(b"s3 payload"), StorageName.CardAttachment, "probe.txt", "source.txt")
    assert file and file.storage_type == "s3"
    assert file.original_filename == "source.txt" and file.filename == "probe.txt"
    assert Storage.get_file(file) == b"s3 payload"
    destination = BytesIO()
    assert Storage.download(file.path.split("/")[2], file.storage_name, file.filename, destination)
    assert destination.getvalue() == b"s3 payload"
    assert Storage.delete(file) and objects
    assert Storage.get_file(file) is None
    client.delete_object.assert_not_called()
    client.close.assert_called()
    client.download_fileobj.side_effect = RuntimeError("fixture cloud unavailable")
    assert Storage.get_file(file) is None
    assert not Storage.download_file(file, BytesIO())
    client.delete_object.side_effect = RuntimeError("fixture cloud unavailable")
    assert Storage.delete(file)
    client.upload_fileobj.side_effect = RuntimeError("fixture cloud unavailable")
    client.put_object.side_effect = RuntimeError("fixture cloud unavailable")
    # A selected S3 upload failure must not silently store data locally.
    assert Storage.upload_named(BytesIO(b"new"), StorageName.CardAttachment, "fresh.txt", "source.txt") is None
