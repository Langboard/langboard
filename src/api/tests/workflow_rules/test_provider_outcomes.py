"""Definite provider rejection stays distinct from ambiguous transport failure."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import pytest
from langboard_shared.domain.services.factory.CardCommentCompactionService import (
    CommentCompactionError,
    request_compaction_draft,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [("generation_failed", "generation_failed"), ("model_unconfigured", "provider_unconfigured")])
async def test_explicit_provider_rejection_has_exact_failure_classification(monkeypatch, status, code):
    payload = json.dumps({"generated": False, "status": status}).encode()

    class Response:
        def raise_for_status(self):
            pass

        async def aiter_bytes(self, **kwargs):
            yield payload

    class Context:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *args):
            pass

    class Client(Context):
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        def stream(self, *args, **kwargs):
            return Context()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr("langboard_shared.core.security.GraphServiceAuth.graph_service_headers", lambda: {})
    snapshot = Mock()
    snapshot.model_dump.return_value = {"sources": [], "prior_summaries": []}
    with pytest.raises(CommentCompactionError) as error:
        await request_compaction_draft(SimpleNamespace(value=json.dumps({"agent_llm": "configured"})), snapshot)
    assert error.value.code == code
    snapshot.validate_draft_json.assert_not_called()
