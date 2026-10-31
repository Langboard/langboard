"""Official OpenAI-compatible embeddings for an explicitly configured internal AI binding."""

from json import dumps, loads
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from .DocumentRetrievalSettings import DocumentRetrievalSettings


if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings


SUPPORTED_PROVIDERS = frozenset({"OpenAI", "OpenAI Compatible", "LiteLLM"})
# Embedding POST execution is ambiguous after transport/server failure. An SDK
# retry can repeat paid inference even while the local cache retains its claim.
EMBEDDING_MAX_RETRIES = 0
EMBEDDING_LEASE_MARGIN_SECONDS = 30


def embedding_inference_budget_seconds(timeout_seconds: float) -> float:
    """Bound undispatched claims and waiter latency, never remote execution."""
    return timeout_seconds + EMBEDDING_LEASE_MARGIN_SECONDS


def snapshot_embedding_config(value: str, binding_uid: str, *, explicit: bool = False) -> dict | None:
    """Freeze non-secret upload settings; only an explicit action can bypass OFF."""
    config, settings = validate_embedding_config(value)
    if not settings.enabled and not explicit:
        return None
    if not isinstance(binding_uid, str) or not binding_uid:
        raise ValueError("An authoritative embedding binding is required")
    return {
        "binding_uid": binding_uid,
        "agent_llm": config["agent_llm"],
        "base_url": config["base_url"].strip().rstrip("/"),
        "model_name": config["model_name"].strip(),
        "retrieval": settings.model_dump(mode="json"),
    }


def resolve_embedding_snapshot(snapshot: dict, current_value: str) -> str:
    """Refresh secrets without replacing the snapshotted model or splitter."""
    current, _ = validate_embedding_config(current_value)
    if current["base_url"].strip().rstrip("/") != snapshot.get("base_url"):
        raise ValueError("Embedding endpoint changed; explicitly request processing again")
    if str(current.get("retrieval", {}).get("external_url") or "").rstrip("/") != str(
        snapshot.get("retrieval", {}).get("external_url") or ""
    ).rstrip("/"):
        raise ValueError("Vector endpoint changed; explicitly request processing again")
    resolved = {
        **snapshot,
        "api_key": current.get("api_key", ""),
        "retrieval": {
            **snapshot.get("retrieval", {}),
            **(
                {"external_api_key": current.get("retrieval", {}).get("external_api_key")}
                if current.get("retrieval", {}).get("external_api_key")
                else {}
            ),
        },
    }
    resolved.pop("binding_uid", None)
    validate_embedding_config(dumps(resolved))
    return dumps(resolved)


def validate_embedding_config(value: str) -> tuple[dict, DocumentRetrievalSettings]:
    config = loads(value)
    if not isinstance(config, dict) or config.get("agent_llm") not in SUPPORTED_PROVIDERS:
        raise ValueError("Select an OpenAI-compatible embedding provider")
    if not isinstance(config.get("model_name"), str) or not config["model_name"].strip():
        raise ValueError("An embedding model is required")
    base_url = config.get("base_url")
    if not isinstance(base_url, str):
        raise ValueError("An explicit embedding endpoint is required")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid embedding endpoint")
    if not isinstance(config.get("api_key", ""), str):
        raise ValueError("Invalid embedding credential")
    return config, DocumentRetrievalSettings.model_validate(config.get("retrieval", {}))


def create_document_embeddings(value: str, allowed_base_urls: set[str]) -> "Embeddings":
    """Construction never sends a document, probes a model or starts indexing.

    Resolve deployment-approved endpoints before the official client can send
    text. Explicit credentials prevent ambient OpenAI environment fallback.
    """
    from langchain_openai import OpenAIEmbeddings

    config, settings = validate_embedding_config(value)
    base_url = config["base_url"].strip().rstrip("/")
    if base_url not in {url.strip().rstrip("/") for url in allowed_base_urls}:
        raise ValueError("Embedding endpoint must be approved in MODEL_PROVIDER_ALLOWED_BASE_URLS")
    return OpenAIEmbeddings(
        model=config["model_name"].strip(),
        base_url=base_url,
        api_key=config.get("api_key") or "not-required",
        check_embedding_ctx_length=False,
        chunk_size=32,
        max_retries=EMBEDDING_MAX_RETRIES,
        request_timeout=settings.timeout_seconds,
    )


def validated_embeddings(embeddings: "Embeddings", dimensions: int) -> "Embeddings":
    """Apply the same finite-vector contract before any backend receives vectors."""
    from math import isfinite
    from langchain_core.embeddings import Embeddings

    if type(dimensions) is not int or not 1 <= dimensions <= 65536:
        raise ValueError("dimensions must be an integer from 1 to 65536")

    class ValidatedEmbeddings(Embeddings):
        """Reject invalid upstream vectors before the official transaction commits."""

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            vectors = embeddings.embed_documents(texts)
            if len(vectors) != len(texts):
                raise ValueError("Embedding provider returned an unexpected vector count")
            return [self._validate(vector) for vector in vectors]

        def embed_query(self, text: str) -> list[float]:
            return self._validate(embeddings.embed_query(text))

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            vectors = await embeddings.aembed_documents(texts)
            if len(vectors) != len(texts):
                raise ValueError("Embedding provider returned an unexpected vector count")
            return [self._validate(vector) for vector in vectors]

        async def aembed_query(self, text: str) -> list[float]:
            return self._validate(await embeddings.aembed_query(text))

        @staticmethod
        def _validate(vector: list[float]) -> list[float]:
            if len(vector) != dimensions or any(
                type(value) not in (int, float) or not isfinite(value) for value in vector
            ):
                raise ValueError("Embedding vector dimension or numeric values do not match the configured model")
            return vector

    return ValidatedEmbeddings()


def embedding_generation_freshness(embedding: dict, current: dict | None) -> dict:
    """Compare generation-defining settings only; return bounded non-secret reasons."""
    from .DocumentEmbeddingCache import CHUNK_VERSION
    from .DocumentVectorGeneration import embedding_fingerprint

    if current is None:
        return {"status": "unavailable", "reasons": ["current_binding_unavailable"]}
    snapshot = embedding.get("config")
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("retrieval"), dict)
        or not {"dimensions", "store", "splitter"}.issubset(snapshot["retrieval"])
    ):
        return {"status": "unknown", "reasons": ["snapshot_unavailable"]}
    try:
        recorded, old = validate_embedding_config(dumps(snapshot))
        active, new = validate_embedding_config(dumps(current))
    except (ValueError, TypeError):
        return {"status": "unknown", "reasons": ["snapshot_unavailable"]}
    reasons = []
    if recorded["agent_llm"] != active["agent_llm"] or recorded["base_url"].rstrip("/") != active["base_url"].rstrip(
        "/"
    ):
        reasons.append("provider")
    if recorded["model_name"].strip() != active["model_name"].strip():
        reasons.append("model")
    if old.dimensions != new.dimensions:
        reasons.append("dimensions")
    if old.splitter != new.splitter:
        reasons.append("splitter")
    if (old.store, str(old.external_url or "").rstrip("/"), old.collection_prefix) != (
        new.store,
        str(new.external_url or "").rstrip("/"),
        new.collection_prefix,
    ):
        reasons.append("storage")
    pointer = embedding.get("pointer")
    if not isinstance(pointer, dict) or not embedding.get("chunk_version"):
        return {"status": "unknown", "reasons": ["generation_schema_unavailable"]}
    fingerprint = embedding_fingerprint(
        provider=recorded["base_url"].strip().rstrip("/"),
        model=recorded["model_name"].strip(),
        dimensions=old.dimensions,
        version="v1",
    )
    if embedding["chunk_version"] != CHUNK_VERSION or pointer.get("embedding_fingerprint") != fingerprint:
        reasons.append("index_schema")
    return {"status": "outdated" if reasons else "current", "reasons": reasons}
