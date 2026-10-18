"""Recovery imports do not initialize request-source generation."""

import os
import subprocess
import sys
from enum import Enum
import pytest
from langboard_shared.core.routing import ApiSchemaHelper
from langboard_shared.core.routing.AppExceptionHandlingRoute import AppExceptionHandlingRoute
from pydantic import BaseModel, Field


@pytest.mark.parametrize(
    "module",
    [
        "langboard.apps.GitHubHealthWorker",
        "langboard_shared.tasks.webhooks.ExecutionOutboxWorker",
        "langboard_shared.tasks.webhooks.AppEventDeliveryWorker",
    ],
)
def test_recovery_import_does_not_load_schema_generator(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import importlib, sys; importlib.import_module({module!r}); "
                "assert 'langboard_shared.core.utils.datamodel.parser.jsonschema' not in sys.modules; "
                "assert 'inflect' not in sys.modules"
            ),
        ],
        env={**os.environ, "SENTRY_DSN": ""},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_schema_generation_still_produces_executable_request_models():
    class Mode(str, Enum):
        active = "active"
        closed = "closed"

    class Query(BaseModel):
        limit: int = Field(default=20, ge=1, le=100)

    class Form(BaseModel):
        title: str
        mode: Mode

    def endpoint():
        pass

    endpoint._schema = {"query": Query, "form": Form, "permission": "*", "file_field": None}
    route = AppExceptionHandlingRoute("/items/{item_uid}", endpoint, methods=["POST"])
    schema = ApiSchemaHelper.create_schema(route)
    namespace = {}
    exec(schema["request_schema_source"], namespace)
    request = namespace["RequestForm"](item_uid="card", form_title="Example", form_mode="active")
    assert request.query_limit == 20
    assert request.form_title == "Example"
    assert request.form_mode.value == "active"
    with pytest.raises(ValueError):
        namespace["RequestForm"](item_uid="card", query_limit=0, form_title="Example", form_mode="active")
    with pytest.raises(ValueError):
        namespace["RequestForm"](item_uid="card", form_title="Example", form_mode="invalid")
