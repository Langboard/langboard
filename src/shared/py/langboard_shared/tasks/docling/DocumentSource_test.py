from unittest.mock import Mock
import pytest
from langboard_shared.tasks.docling.DocumentCleanupTask import record_cleanup
from langboard_shared.tasks.docling.DocumentSource import document_source_identity


def project_source():
    return dict(
        source_type="project_attachment",
        board_uid="board",
        attachment_uid="asset",
        content_hash="hash",
        embedding_fingerprint="a" * 64,
    )


@pytest.mark.parametrize("kind", [None, "card_attachment", "project_attachment"])
def test_identity_preserves_explicit_and_legacy_shapes(kind):
    source = project_source()
    if kind != "project_attachment":
        source["card_uid"] = "card"
    if kind is None:
        del source["source_type"]
    else:
        source["source_type"] = kind
    assert document_source_identity(source) == source
    with pytest.raises(ValueError, match="Incomplete"):
        document_source_identity(source, require_generation=True)
    assert document_source_identity({**source, "generation": "g"}, require_generation=True) == {
        **source,
        "generation": "g",
    }


@pytest.mark.parametrize(
    "update",
    [
        {"source_type": None},
        {"source_type": "wiki"},
        {"source_type": []},
        {"card_uid": "card"},
        {"card_uid": None},
        {"wiki_uid": None},
        {"board_uid": ""},
        {"attachment_uid": None},
        {"content_hash": 1},
        {"embedding_fingerprint": False},
    ],
)
def test_invalid_identity_rejected(update):
    with pytest.raises(ValueError):
        document_source_identity({**project_source(), **update})


@pytest.mark.parametrize("explicit", [True, False])
def test_cleanup_retains_source_identity_and_strips_credentials(explicit):
    pointer = {
        **project_source(),
        "generation": "g",
        "chunk_ids": ["chunk"],
        "secret": "hidden",
        "storage": {"type": "sqlite", "api_key": "hidden"},
    }
    if not explicit:
        del pointer["source_type"]
        pointer["card_uid"] = "card"
    db = Mock()
    assert record_cleanup(db, pointer, dispatch=False)
    recorded = db.insert.call_args.args[0].pointer
    assert recorded == {key: value for key, value in pointer.items() if key != "secret"} | {
        "storage": {"type": "sqlite"}
    }


def test_wiki_source_requires_exact_owner_and_cleanup_preserves_it():
    source = dict(
        source_type="wiki_attachment",
        board_uid="board",
        wiki_uid="wiki",
        attachment_uid="attachment",
        content_hash="hash",
        embedding_fingerprint="model",
        generation="generation",
    )
    assert document_source_identity(source, require_generation=True) == source
    for invalid in ({key: value for key, value in source.items() if key != "wiki_uid"}, {**source, "card_uid": "card"}):
        with pytest.raises(ValueError):
            document_source_identity(invalid)
