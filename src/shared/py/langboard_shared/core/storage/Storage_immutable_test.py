"""Provider publication tests; official S3 client fixtures make no AWS requests."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
import pytest
from langboard_shared.Env import Env
from .LocalStorage import LocalStorage
from .S3Storage import S3Storage
from .StorageName import StorageName


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(type(Env), "LOCAL_STORAGE_DIR", property(lambda _: tmp_path))
    monkeypatch.setenv("COMMON_SECRET_KEY", "fixture-only-immutable")
    return LocalStorage()


def publish(storage, payload=b"winner"):
    return storage.upload_immutable(BytesIO(payload), "source.txt", StorageName.CardAttachment, "object.txt")


def test_local_first_writer_wins_and_legacy_named_still_overwrites(local):
    assert publish(local)
    assert publish(local, b"loser")
    assert local.get("card_attachment", "object.txt") == b"winner"
    assert local.upload_named(BytesIO(b"legacy"), "source.txt", StorageName.CardAttachment, "object.txt")
    assert local.get("card_attachment", "object.txt") == b"legacy"


def test_local_stale_publisher_cannot_expose_partial_or_replace_winner(local, tmp_path):
    started, resume = Event(), Event()

    class Paused(BytesIO):
        def read(self, size=-1):
            if self.tell():
                started.set()
                assert resume.wait(10)
            return super().read(min(size, 3))

    with ThreadPoolExecutor(max_workers=1) as workers:
        stale = workers.submit(
            local.upload_immutable, Paused(b"stale"), "s.txt", StorageName.CardAttachment, "object.txt"
        )
        try:
            assert started.wait(10)
            assert local.get("card_attachment", "object.txt") is None
            assert publish(local)
        finally:
            resume.set()
        assert stale.result()
    assert local.get("card_attachment", "object.txt") == b"winner"
    assert [p.name for p in (tmp_path / "card_attachment").iterdir()] == ["object.txt"]


def test_local_failed_stream_removes_only_own_temp(local, tmp_path):
    assert publish(local)
    other = tmp_path / "card_attachment" / ".another-writer.tmp"
    other.write_bytes(b"pending")

    class Failed(BytesIO):
        def read(self, size=-1):
            if self.tell():
                raise OSError("fixture interrupted stream")
            return super().read(2)

    assert local.upload_immutable(Failed(b"bad"), "s.txt", StorageName.CardAttachment, "new.txt") is None
    assert local.get("card_attachment", "new.txt") is None
    assert local.get("card_attachment", "object.txt") == b"winner"
    assert other.read_bytes() == b"pending"
    assert sorted(p.name for p in other.parent.iterdir()) == [".another-writer.tmp", "object.txt"]


@pytest.fixture
def s3(monkeypatch):
    import boto3
    from botocore.stub import Stubber

    monkeypatch.setenv("COMMON_SECRET_KEY", "fixture-only-immutable")
    monkeypatch.setattr(type(Env), "S3_BUCKET_NAME", property(lambda _: "fixture-bucket"))
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture")
    monkeypatch.setattr(S3Storage, "_connect_client", lambda _: client)
    with Stubber(client) as stub:
        yield S3Storage(), client, stub
        stub.assert_no_pending_responses()


def params(**extra):
    return {"Bucket": "fixture-bucket", "Key": "card_attachment/object.txt", **extra}


def test_s3_native_simple_conditional_request(s3):
    storage, _, stub = s3
    stub.add_response("put_object", {}, params(Body=b"winner", IfNoneMatch="*"))
    model = publish(storage)
    assert model and model.filename == "object.txt" and model.storage_name == "card_attachment"


@pytest.mark.parametrize(
    "code,status,existing",
    [
        ("PreconditionFailed", 412, True),
        ("412", 412, True),
        ("ConditionalRequestConflict", 409, False),
        ("NotImplemented", 501, False),
        ("AccessDenied", 403, False),
    ],
)
def test_s3_native_simple_failure_is_closed(s3, code, status, existing):
    storage, _, stub = s3
    stub.add_client_error(
        "put_object", code, http_status_code=status, expected_params=params(Body=b"winner", IfNoneMatch="*")
    )
    assert bool(publish(storage)) is existing


def multipart_start(stub, first, second):
    stub.add_response("create_multipart_upload", {"UploadId": "owned-upload"}, params())
    for number, body in enumerate((first, second), 1):
        stub.add_response(
            "upload_part", {"ETag": f"part-{number}"}, params(UploadId="owned-upload", PartNumber=number, Body=body)
        )
    return params(
        UploadId="owned-upload",
        IfNoneMatch="*",
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": "part-1"}, {"PartNumber": 2, "ETag": "part-2"}]},
    )


@pytest.mark.parametrize(
    "code,status",
    [(None, None), ("PreconditionFailed", 412), ("ConditionalRequestConflict", 409), ("NotImplemented", 501)],
)
def test_s3_native_multipart_conditional_complete_and_owned_abort(s3, code, status):
    storage, _, stub = s3
    first, second = b"a" * (8 * 1024 * 1024), b"tail"
    complete = multipart_start(stub, first, second)
    if code:
        stub.add_client_error("complete_multipart_upload", code, http_status_code=status, expected_params=complete)
        stub.add_response("abort_multipart_upload", {}, params(UploadId="owned-upload"))
    else:
        stub.add_response("complete_multipart_upload", {}, complete)

    class Bounded(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 8 * 1024 * 1024
            return super().read(size)

    result = storage.upload_immutable(Bounded(first + second), "source.txt", StorageName.CardAttachment, "object.txt")
    assert bool(result) is (code is None or status == 412)


def test_s3_part_failure_aborts_only_owned_upload(s3):
    storage, _, stub = s3
    stub.add_response("create_multipart_upload", {"UploadId": "owned-upload"}, params())
    stub.add_client_error(
        "upload_part",
        "PreconditionFailed",
        http_status_code=412,
        expected_params=params(UploadId="owned-upload", PartNumber=1, Body=b"a" * (8 * 1024 * 1024)),
    )
    stub.add_response("abort_multipart_upload", {}, params(UploadId="owned-upload"))
    assert publish(storage, b"a" * (8 * 1024 * 1024 + 1)) is None


def test_s3_unsupported_native_schema_fails_before_publication(s3, monkeypatch):
    storage, client, _ = s3
    shape = client.meta.service_model.operation_model("PutObject").input_shape
    monkeypatch.delitem(shape.members, "IfNoneMatch")
    assert publish(storage) is None


def test_local_concurrent_first_writer_wins(local, tmp_path):
    payloads = [f"writer-{number}".encode() for number in range(12)]
    with ThreadPoolExecutor(max_workers=6) as workers:
        assert all(workers.map(lambda payload: publish(local, payload), payloads))
    assert local.get("card_attachment", "object.txt") in payloads
    assert [p.name for p in (tmp_path / "card_attachment").iterdir()] == ["object.txt"]


def test_local_publication_failure_cleans_private_temp(local, tmp_path, monkeypatch):
    from importlib import import_module

    module = import_module(LocalStorage.__module__)

    def denied(*args):
        raise OSError("fixture hard link unavailable")

    monkeypatch.setattr(module, "link", denied)
    assert publish(local) is None
    assert not list((tmp_path / "card_attachment").iterdir())


def test_s3_short_reads_accumulate_before_native_put(s3):
    storage, _, stub = s3
    stub.add_response("put_object", {}, params(Body=b"winner", IfNoneMatch="*"))

    class Short(BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    assert storage.upload_immutable(Short(b"winner"), "source.txt", StorageName.CardAttachment, "object.txt")


def test_s3_stream_failure_aborts_owned_upload_even_if_abort_fails(s3):
    storage, _, stub = s3
    first = b"a" * (8 * 1024 * 1024)
    multipart_start(stub, first, first)
    # The stream fails before completion can be attempted.
    stub.add_client_error(
        "abort_multipart_upload", "InternalError", http_status_code=500, expected_params=params(UploadId="owned-upload")
    )

    class Interrupted(BytesIO):
        def read(self, size=-1):
            if self.tell() >= 2 * len(first):
                raise OSError("fixture interrupted multipart stream")
            return super().read(size)

    assert (
        storage.upload_immutable(Interrupted(first + first), "s.txt", StorageName.CardAttachment, "object.txt") is None
    )


@pytest.mark.parametrize("bad", ["", "nested/object.txt"])
def test_invalid_immutable_filename_fails_before_writes(local, s3, bad):
    storage, _, _ = s3
    for provider in (local, storage):
        assert provider.upload_immutable(BytesIO(b"data"), "source.txt", StorageName.CardAttachment, bad) is None
