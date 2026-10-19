"""Read-only operator preflight for an optional Elasticsearch connection."""

import os
from json import dumps
from langboard_shared.core.bootstrap import BaseCommand, BaseCommandOptions
from langboard_shared.core.search.ElasticsearchKeywordSearch import check_elasticsearch_connection
from pydantic import Field


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
