"""Read-only operator preflight for an optional Elasticsearch connection."""

import os
import re
from json import dumps
from pathlib import Path
from urllib.parse import urlsplit
from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from pydantic import Field


def check_elasticsearch_connection(
    endpoint: str, index: str, api_key: str, *, ca_certs: str = "", timeout: float = 5, require_write: bool = False
) -> dict:
    """Check an operator-supplied target without indexing or returning remote data."""
    result = {"ok": False, "data_mutated": False}
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
            or not api_key
            or any(ord(char) < 33 for char in api_key)
            or type(timeout) not in (int, float)
            or not 1 <= timeout <= 30
            or type(require_write) is not bool
            or (ca_certs and not Path(ca_certs).is_file())
        ):
            return {**result, "code": "invalid_configuration"}
        _ = url.port
    except (ValueError, OSError):
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
        with Elasticsearch(
            endpoint,
            api_key=api_key,
            ca_certs=ca_certs or None,
            verify_certs=True,
            request_timeout=timeout,
            max_retries=0,
            retry_on_timeout=False,
        ) as client:
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


class CheckKeywordSearchCommandOptions(BaseCommandOptions):
    endpoint: str = Field(default="", description="Explicit HTTPS Elasticsearch endpoint")
    index: str = Field(default="", description="One instance-owned index, without wildcards or aliases")
    api_key_env: str = Field(
        default="LANGBOARD_ELASTICSEARCH_API_KEY", description="Environment variable containing the API key"
    )
    ca_certs: str = Field(default="", description="CA bundle file; empty uses the client's trusted defaults")
    timeout: int = Field(default=5, description="Per-request timeout in seconds, from 1 to 30")
    require_write: bool = Field(
        default=False, description="Also check index/delete privileges, without performing writes"
    )


class CheckKeywordSearchCommand(BaseCommand):
    @staticmethod
    def is_only_in_dev() -> bool:
        return False

    @property
    def option_class(self) -> type[CheckKeywordSearchCommandOptions]:
        return CheckKeywordSearchCommandOptions

    @property
    def command(self) -> str:
        return "search:check"

    @property
    def positional_name(self) -> str:
        return ""

    @property
    def description(self) -> str:
        return "Check an optional Elasticsearch connection without enabling search or indexing"

    @property
    def choices(self) -> list[str] | None:
        return None

    @property
    def store_type(self) -> type[bool]:
        return bool

    def execute(self, options: CheckKeywordSearchCommandOptions) -> None:
        result = check_elasticsearch_connection(
            options.endpoint,
            options.index,
            os.environ.get(options.api_key_env, ""),
            ca_certs=options.ca_certs,
            timeout=options.timeout,
            require_write=options.require_write,
        )
        print(dumps(result))
        if not result["ok"]:
            raise SystemExit(1)
