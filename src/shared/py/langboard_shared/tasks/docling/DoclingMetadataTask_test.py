"""Absent optional conversion support must fail clearly before file IO."""

import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.domain.models import CardMetadata
from langboard_shared.domain.models.InternalBot import InternalBotType
from langboard_shared.tasks.docling import DoclingMetadataTask as task


def _worker_source():
    attachment = SimpleNamespace(get_uid=lambda: "attachment", card_id=1, filename="report.pdf", file="fixture-source")
    card = SimpleNamespace(project_id=2)
    metadata = Mock()
    metadata.get_document_by_attachment_uid.return_value = {
        "status": "processing",
        "vision_config": {"binding_uid": "vision", "model_name": "upload-model", "base_url": "https://example.test/v1"}
    }
    service = SimpleNamespace(
        internal_bot=SimpleNamespace(
            get_by_id_like=lambda _: SimpleNamespace(
                bot_type=InternalBotType.DocumentVision,
                value='{"api_key":"test-key","model_name":"changed-global-model","base_url":"https://example.test/v1"}',
            )
        ),
        card_attachment=SimpleNamespace(get_by_id_like=lambda _: attachment),
        card=SimpleNamespace(get_by_id_like=lambda _: card),
        project=SimpleNamespace(get_by_id_like=lambda _: object()),
        docling_metadata=metadata,
    )
    service.internal_bot.get_current_by_id_like = Mock(side_effect=service.internal_bot.get_by_id_like)
    return service, attachment, card, metadata


@pytest.fixture(autouse=True)
def fixture_extraction_cache(monkeypatch):
    """Simple worker tests isolate SQL through an explicit external cache fixture."""
    @contextmanager
    def cache(**kwargs):
        validate = kwargs["on_validate"]

        def convert(callback):
            validate()
            result = callback()
            validate()
            return result

        yield SimpleNamespace(token="a" * 32, get_or_convert=convert, retain=Mock())

    monkeypatch.setattr(task, "cached_document_extraction", cache)
    monkeypatch.setattr(task.DbSession, "atomic", contextmanager(lambda: iter([None])))


def test_missing_worker_package_preserves_attachment_and_skips_file_work(monkeypatch):
    service, attachment, card, metadata = _worker_source()
    monkeypatch.setattr(task, "find_spec", lambda _: None, raising=False)
    download = Mock(side_effect=AssertionError("Missing package must not download"))
    convert = Mock(side_effect=AssertionError("Missing package must not spawn conversion"))
    monkeypatch.setattr(task.Storage, "download_file", download)
    monkeypatch.setattr(task, "_convert_to_markdown", convert)

    task._index_card_attachment(service, attachment)

    download.assert_not_called()
    convert.assert_not_called()
    metadata.mark_document_indexed.assert_not_called()
    metadata.mark_document_failed.assert_called_once_with(
        CardMetadata,
        card,
        "attachment",
        "report.pdf",
        "Document processing failed; check the worker document-processing extra, provider configuration "
        "and source availability, then retry this attachment.",
        generation=None,
    )
    metadata.publish_update.assert_called_once()


def test_unrequested_attachment_never_starts_processing(monkeypatch):
    attachment = SimpleNamespace(get_uid=lambda: "attachment", card_id=1, filename="report.pdf")
    card = SimpleNamespace(project_id=2)
    metadata = Mock()
    metadata.get_document_by_attachment_uid.return_value = None
    binding = Mock(side_effect=AssertionError("Global settings must not trigger old attachments"))
    service = SimpleNamespace(
        internal_bot=SimpleNamespace(get_by_id_like=binding),
        card_attachment=SimpleNamespace(get_by_id_like=lambda _: attachment),
        card=SimpleNamespace(get_by_id_like=lambda _: card),
        project=SimpleNamespace(get_by_id_like=lambda _: object()),
        docling_metadata=metadata,
    )
    download = Mock()
    monkeypatch.setattr(task.Storage, "download_file", download)
    task._index_card_attachment(service, attachment)
    binding.assert_not_called()
    download.assert_not_called()
    metadata.claim_document.assert_not_called()
    metadata.mark_document_failed.assert_not_called()


def test_converter_process_streams_pages_and_keywords_without_exposing_stdin(monkeypatch, tmp_path):
    script = tmp_path / "converter.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "config = json.loads(sys.stdin.read())\n"
        "assert config['api_key'] == 'fixture-private-key'\n"
        "print('LANGBOARD_DOCLING_PROGRESS:not-json', flush=True)\n"
        'print(\'LANGBOARD_DOCLING_PROGRESS:{"completed_pages": 3, "total_pages": 2}\', flush=True)\n'
        'print(\'LANGBOARD_DOCLING_PROGRESS:{"completed_pages": 1, "total_pages": 2}\', flush=True)\n'
        'print(\'LANGBOARD_DOCLING_KEYWORDS:{"keywords": {"ko": ["문서"]}}\', flush=True)\n'
        'print(\'LANGBOARD_DOCLING_PROGRESS:{"completed_pages": 2, "total_pages": 2}\', flush=True)\n'
        "pathlib.Path(sys.argv[1]).write_text('faithful original', encoding='utf-8')\n"
        "pathlib.Path(sys.argv[2]).write_text(json.dumps({'schema_name':'DoclingDocument','pages':{'2':{}}}))\n",
        encoding="utf-8",
    )
    output_paths = []

    def launch(command, **kwargs):
        assert "fixture-private-key" not in " ".join(command)
        document_path = command[command.index("--document-output") + 1]
        output_paths.extend([command[4], document_path])
        return subprocess.Popen([sys.executable, str(script), command[4], document_path], **kwargs)

    monkeypatch.setattr(task, "Popen", launch)
    progress, keywords, structural = [], [], []
    result = task._convert_to_markdown(
        "fixture.pdf",
        lambda done, total: progress.append((done, total)),
        vision_value='{"api_key":"fixture-private-key"}',
        on_keywords=keywords.append,
        on_document=structural.append,
    )
    assert result == "faithful original"
    assert progress == [(1, 2), (2, 2)]
    assert keywords == [{"ko": ["문서"]}]
    assert structural == [{"schema_name": "DoclingDocument", "pages": {"2": {}}}]
    assert not any(task.Path(path).exists() for path in output_paths)


def test_converter_timeout_kills_process_and_removes_partial_output(monkeypatch, tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    processes, output_paths = [], []

    def launch(command, **kwargs):
        output_paths.append(command[4])
        process = subprocess.Popen([sys.executable, str(script)], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(task, "Popen", launch)
    monkeypatch.setattr(task, "Env", SimpleNamespace(DOCLING_CONVERSION_TIMEOUT_SECONDS=0.1))
    with pytest.raises(subprocess.TimeoutExpired):
        task._convert_to_markdown("fixture.pdf")
    assert all(process.poll() is not None for process in processes)
    assert not any(task.Path(path).exists() for path in output_paths)


def test_upload_binding_snapshot_survives_global_model_change(monkeypatch):
    from json import loads

    service, attachment, card, metadata = _worker_source()
    monkeypatch.setattr(task, "find_spec", lambda _: object())

    def download(_, output):
        output.write(b"source")
        return True

    monkeypatch.setattr(task.Storage, "download_file", download)
    convert = Mock(return_value="original text")
    monkeypatch.setattr(task, "_convert_to_markdown", convert)
    task._index_card_attachment(service, attachment)
    config = loads(convert.call_args.kwargs["vision_value"])
    assert config["model_name"] == "upload-model"
    assert config["api_key"] == "test-key"
    assert "binding_uid" not in config
    metadata.mark_document_indexed.assert_called_once()
    metadata.mark_document_failed.assert_not_called()


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("change", ["deleted", "type", "endpoint"])
def test_vision_binding_current_primary_fences_download_and_publication(monkeypatch, when, change):
    service, attachment, _card, metadata = _worker_source()
    current = service.internal_bot.get_by_id_like("vision")
    service.internal_bot.get_current_by_id_like = Mock(side_effect=lambda _: current)
    monkeypatch.setattr(task, "find_spec", lambda _: object())

    def revoke():
        nonlocal current
        if change == "deleted":
            current = None
        elif change == "type":
            current.bot_type = InternalBotType.DocumentEmbedding
        else:
            current.value = '{"api_key":"new-secret","base_url":"https://changed.invalid/v1"}'

    def download(_source, output):
        output.write(b"source")
        return True

    def convert(_path, **_kwargs):
        if when == "after":
            revoke()
        return "private provider response"

    io = Mock(side_effect=download)
    monkeypatch.setattr(task.Storage, "download_file", io)
    monkeypatch.setattr(task, "_convert_to_markdown", Mock(side_effect=convert))
    if when == "before":
        revoke()
    task._index_card_attachment(service, attachment)
    metadata.mark_document_indexed.assert_not_called()
    metadata.mark_document_failed.assert_called_once()
    if when == "before":
        io.assert_not_called()
        task._convert_to_markdown.assert_not_called()
    else:
        task._convert_to_markdown.assert_called_once()
    assert "new-secret" not in str(metadata.mark_document_failed.call_args)


@pytest.mark.parametrize("boundary", ["download", "convert"])
def test_worker_errors_never_publish_credentials_or_document_content(monkeypatch, boundary):
    service, attachment, _card, metadata = _worker_source()
    monkeypatch.setattr(task, "find_spec", lambda _: object())
    secret = "fixture-credential-and-private-document"

    def download(_source, output):
        if boundary == "download":
            raise RuntimeError(secret)
        output.write(b"source")
        return True

    monkeypatch.setattr(task.Storage, "download_file", download)
    monkeypatch.setattr(task, "_convert_to_markdown", Mock(side_effect=RuntimeError(secret)))
    task._index_card_attachment(service, attachment)
    metadata.mark_document_failed.assert_called_once()
    assert secret not in str(metadata.mark_document_failed.call_args)
    metadata.mark_document_indexed.assert_not_called()


@pytest.mark.parametrize("invalid", ["hash", "size", "generation", "filename", "source"])
def test_invalid_card_source_cannot_reach_cache_or_publish(monkeypatch, invalid):
    service, attachment, _card, metadata = _worker_source()
    monkeypatch.setattr(task, "find_spec", lambda _: object())
    convert = Mock(return_value="private text")
    monkeypatch.setattr(task, "_convert_to_markdown", convert)
    cache = Mock(side_effect=AssertionError("Invalid source must not enter cache"))
    monkeypatch.setattr(task, "cached_document_extraction", cache)
    monkeypatch.setattr(task.DbSession, "atomic", contextmanager(lambda: iter([None])))
    attachment.content_sha256 = task.sha256(b"source").hexdigest()

    def download(_, output):
        if invalid == "size":
            output.write(b"x" * (task.Env.MAX_FILE_SIZE_MB * 1024 * 1024 + 1))
        else:
            output.write(b"changed" if invalid == "hash" else b"source")
        if invalid == "generation":
            metadata.get_document_by_attachment_uid.return_value["generation"] = "changed"
        elif invalid == "filename":
            attachment.filename = "changed.exe"
        elif invalid == "source":
            service.card_attachment.get_by_id_like = lambda _: None
        return True

    monkeypatch.setattr(task.Storage, "download_file", download)
    task._index_card_attachment(service, attachment)
    cache.assert_not_called()
    convert.assert_not_called()
    metadata.mark_document_indexed.assert_not_called()


def test_legacy_card_hash_comes_from_download_and_token_stays_private(monkeypatch):
    service, attachment, _card, metadata = _worker_source()
    monkeypatch.setattr(task, "find_spec", lambda _: object())
    monkeypatch.setattr(task.Storage, "download_file", lambda _, output: bool(output.write(b"source")))
    monkeypatch.setattr(task, "_convert_to_markdown", Mock(return_value="legacy text"))
    task._index_card_attachment(service, attachment)
    published = metadata.mark_document_indexed.call_args.kwargs
    assert published["content_hash"] == task.sha256(b"source").hexdigest()
    assert published["extraction_cache_token"] == "a" * 32
    assert "extraction_cache_token" not in published["content"]
