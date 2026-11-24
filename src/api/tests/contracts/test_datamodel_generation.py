"""Keep generated consumer models byte-compatible across shared constructor cleanup."""

import ast
import contextlib
import io
import json
from pathlib import Path
import pytest
from langboard_shared.core.utils.datamodel import DataModelType, InputFileType, PythonVersion, generate


_EXPECTED = json.loads((Path(__file__).parent / "fixtures/datamodel_outputs.json").read_text())
_SCHEMA = {
    "title": "Receipt",
    "type": "object",
    "required": ["name", "created"],
    "properties": {
        "name": {"type": "string"},
        "created": {"type": "string", "format": "date-time"},
        "date": {"type": "string", "format": "date"},
        "time": {"type": "string", "format": "time"},
        "choice": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
        "items": {"type": "array", "items": {"type": "object", "properties": {"value": {"type": "number"}}}},
        "state": {"type": "string", "enum": ["open", "closed"]},
    },
}


@pytest.mark.parametrize("key", sorted(_EXPECTED))
def test_existing_consumer_models_remain_unchanged(key):
    model, version, standard = key.split(":")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        generate(
            json.dumps(_SCHEMA),
            input_file_type=InputFileType.JsonSchema,
            output_model_type=DataModelType(model),
            target_python_version=PythonVersion(version),
            use_standard_collections=standard == "True",
            disable_timestamp=True,
        )
    generated = output.getvalue()
    assert generated == _EXPECTED[key]
    ast.parse(generated)
