"""Optional official-client keyword reads; callers revalidate source data and ACL."""

import re
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit


def validated_destination(endpoint: str, index: str) -> dict[str, str]:
    """Non-secret identity for a server-owned connection; no network or key lookup."""
    try:
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(ord(char) < 33 for char in endpoint)
            or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", index)
        ):
            raise ValueError("Invalid keyword destination")
        _ = url.port
    except (ValueError, TypeError):
        raise ValueError("Invalid keyword destination") from None
    return {"endpoint": endpoint.rstrip("/"), "index": index}


def _connection_options(endpoint: str, index: str, api_key: str, ca_certs: str, timeout: float) -> dict:
    destination = validated_destination(endpoint, index)
    try:
        if (
            not api_key
            or any(ord(char) < 33 for char in api_key)
            or type(timeout) not in (int, float)
            or not 1 <= timeout <= 30
            or (ca_certs and not Path(ca_certs).is_file())
        ):
            raise ValueError("Invalid Elasticsearch configuration")
    except (ValueError, OSError):
        raise ValueError("Invalid Elasticsearch configuration") from None
    return dict(
        hosts=destination["endpoint"],
        api_key=api_key,
        ca_certs=ca_certs or None,
        verify_certs=True,
        request_timeout=timeout,
        max_retries=0,
        retry_on_timeout=False,
    )


def check_elasticsearch_connection(
    endpoint: str, index: str, api_key: str, *, ca_certs: str = "", timeout: float = 5, require_write: bool = False
) -> dict:
    """Check an operator-supplied target without indexing or returning remote data."""
    result = {"ok": False, "data_mutated": False}
    try:
        options = _connection_options(endpoint, index, api_key, ca_certs, timeout)
        if type(require_write) is not bool:
            raise ValueError("Invalid privilege mode")
    except ValueError:
        return {**result, "code": "invalid_configuration"}

    try:
        from elastic_transport import ConnectionError, ConnectionTimeout, TlsError
        from elasticsearch import (
            ApiError,
            AuthenticationException,
            AuthorizationException,
            Elasticsearch,
            NotFoundError,
            UnsupportedProductError,
            __version__,
        )
    except ImportError:
        return {**result, "code": "keyword_search_extra_required"}

    privileges = ["read", "view_index_metadata"] + (["index", "delete"] if require_write else [])
    phase = "privileges"
    try:
        with Elasticsearch(**options) as client:
            permissions = client.security.has_privileges(
                cluster=["monitor"], index=[{"names": [index], "privileges": privileges}]
            )
            granted = permissions.get("index", {}).get(index, {})
            missing = [name for name in privileges if granted.get(name) is not True]
            if missing:
                return {**result, "code": "insufficient_index_privileges", "missing": missing}
            phase = "target"
            target = client.indices.resolve_index(name=index, expand_wildcards="none")
            if (
                target.get("aliases")
                or target.get("data_streams")
                or [entry.get("name") for entry in target.get("indices", [])] != [index]
            ):
                return {**result, "code": "concrete_index_required"}
            version = None
            # Do not require cluster-wide monitoring permission merely to search.
            if permissions.get("cluster", {}).get("monitor") is True:
                phase = "version"
                version = client.info().get("version", {}).get("number")
                if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
                    return {**result, "code": "invalid_server_response"}
                if int(version.split(".")[0]) != __version__[0]:
                    return {**result, "code": "incompatible_server"}
            phase = "search"
            response = client.search(
                index=index,
                size=0,
                query={"match_none": {}},
                source=False,
                track_total_hits=False,
                allow_partial_search_results=False,
                timeout=f"{int(timeout * 1000)}ms",
            )
            if response.get("timed_out") is not False or response.get("_shards", {}).get("failed") != 0:
                return {**result, "code": "incomplete_search"}
            return {
                **result,
                "ok": True,
                "code": "connection_verified",
                "client_major": __version__[0],
                "server_version": version,
                "version_verified": version is not None,
                "write_permissions_verified": require_write,
            }
    except AuthenticationException:
        return {**result, "code": "authentication_failed"}
    except AuthorizationException:
        return {**result, "code": "permission_denied"}
    except NotFoundError:
        return {**result, "code": "index_unavailable" if phase in {"target", "search"} else "endpoint_unavailable"}
    except TlsError:
        return {**result, "code": "tls_verification_failed"}
    except ConnectionTimeout:
        return {**result, "code": "connection_timeout"}
    except ConnectionError:
        return {**result, "code": "connection_failed"}
    except UnsupportedProductError:
        return {**result, "code": "incompatible_server"}
    except ApiError as error:
        return {**result, "code": "incompatible_server" if error.status_code == 406 else "server_request_failed"}
    except (ValueError, TypeError, AttributeError):
        return {**result, "code": "invalid_server_response"}


def search_document_candidates(project_uid: str, query: str) -> list[dict[str, str]] | None:
    """Optional, bounded supplemental candidates. Never trust their text or authority."""
    if not query.strip() or len(query) > 1000:
        return []
    try:
        if not all(find_spec(name) is not None for name in ("elasticsearch", "elastic_transport")):
            return None
    except ImportError:
        # An absent optional client must not initialize database or vault services.
        return None
    from ...domain.services.KeywordSearchSettings import configuration, runtime_options

    config = None
    try:
        config = configuration(operation="read")
        if config is None:
            return None
        from elasticsearch import Elasticsearch

        index = config["index"]
        options = runtime_options(config, operation="read")
        with Elasticsearch(**options) as client:
            response = client.search(
                index=index,
                size=20,
                source=["active", "board_uid", "card_uid", "attachment_uid", "projection_hash"],
                query={
                    "bool": {
                        "filter": [{"term": {"board_uid": project_uid}}, {"term": {"active": True}}],
                        "must": [{"match": {"text": {"query": query, "operator": "and"}}}],
                    }
                },
                track_total_hits=False,
                allow_partial_search_results=False,
                timeout="500ms",
            )
            body = response.body
    except Exception:
        # Optional provider failures must not remove PostgreSQL search coverage.
        # Client exceptions can contain the key, request or document text.
        return [] if config is not None else None
    return _validated_document_candidates(body, project_uid)


def _validated_document_candidates(body: object, project_uid: str) -> list[dict[str, str]]:
    if not isinstance(body, dict) or body.get("timed_out") is not False:
        return []
    shards = body.get("_shards")
    container = body.get("hits")
    if not isinstance(shards, dict) or shards.get("failed") != 0 or not isinstance(container, dict):
        return []
    hits = container.get("hits")
    if not isinstance(hits, list):
        return []
    result = []
    for hit in hits[:20]:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict) or source.get("board_uid") != project_uid or source.get("active") is not True:
            continue
        if not all(
            isinstance(source.get(key), str) and re.fullmatch(r"[A-Za-z0-9]{11}", source[key])
            for key in ("card_uid", "attachment_uid")
        ):
            continue
        if not isinstance(source.get("projection_hash"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", source["projection_hash"]
        ):
            continue
        result.append({key: source[key] for key in ("card_uid", "attachment_uid", "projection_hash")})
    return result


DOCUMENT_CANDIDATE_BATCH_SIZE = 50


def search_document_candidates_many(project_uids: list[str], query: str) -> dict[str, list[dict[str, str]]] | None:
    """Supplement each distinct board with up to 20 candidates, using bounded native msearch.

    None means the optional client or provider configuration is unavailable; failures yield empty lists only
    for affected boards. Callers must always preserve native SQL search coverage.
    """
    boards = list(dict.fromkeys(project_uids))
    result: dict[str, list[dict[str, str]]] = {board: [] for board in boards}
    if not boards or not query.strip() or len(query) > 1000:
        return result
    try:
        if not all(find_spec(name) is not None for name in ("elasticsearch", "elastic_transport")):
            return None
    except ImportError:
        return None
    from ...domain.services.KeywordSearchSettings import configuration, runtime_options

    config = None
    try:
        config = configuration(operation="read")
        if config is None:
            return None
        from elasticsearch import Elasticsearch

        options = runtime_options(config, operation="read")
        with Elasticsearch(**options) as client:
            for start in range(0, len(boards), DOCUMENT_CANDIDATE_BATCH_SIZE):
                batch = boards[start : start + DOCUMENT_CANDIDATE_BATCH_SIZE]
                searches = []
                for board in batch:
                    searches.extend(
                        [
                            {"allow_partial_search_results": False},
                            {
                                "size": 20,
                                "_source": ["active", "board_uid", "card_uid", "attachment_uid", "projection_hash"],
                                "query": {
                                    "bool": {
                                        "filter": [{"term": {"board_uid": board}}, {"term": {"active": True}}],
                                        "must": [{"match": {"text": {"query": query, "operator": "and"}}}],
                                    }
                                },
                                "track_total_hits": False,
                                "timeout": "500ms",
                            },
                        ]
                    )
                try:
                    body = client.msearch(index=config["index"], searches=searches).body
                    responses = body.get("responses") if isinstance(body, dict) else None
                    if not isinstance(responses, list) or len(responses) > len(batch):
                        continue
                    # Official msearch returns positional responses. Source board
                    # validation prevents a reordered response crossing board scopes.
                    for board, response in zip(batch, responses):
                        if not isinstance(response, dict) or "error" in response:
                            continue
                        status = response.get("status", 200)
                        if type(status) is not int or status != 200:
                            continue
                        result[board] = _validated_document_candidates(response, board)
                except Exception:
                    # Keep successful boards and continue after an optional batch fails.
                    # Never expose provider exception text, keys, or document contents.
                    continue
    except Exception:
        return result if config is not None else None
    return result
