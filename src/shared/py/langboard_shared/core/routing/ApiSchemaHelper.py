from enum import Enum
from json import dumps as json_dumps
from re import findall as re_findall
from typing import Any, Literal, NotRequired, TypedDict, cast
from pydantic import BaseModel
from ..utils.datamodel.parser.jsonschema import JsonSchemaParser
from ..utils.decorators import staticclass
from .ApiPermission import ApiPermission
from .AppExceptionHandlingRoute import AppExceptionHandlingRoute


PATH_PARAM_PATTERN = r"\{([^}]+)\}"


class ApiSchemaMap(TypedDict):
    name: str
    path: str
    path_params: list[str]
    method: str
    permission: str | ApiPermission
    content_type: Literal["application/json", "multipart/form-data"]
    description: str
    form: dict[str, Any] | None
    query: dict[str, Any] | None
    file_field: str | None
    request_schema_source: str | None
    collaborative_edit_targets: NotRequired[list[dict[str, Any]]]


@staticclass
class ApiSchemaHelper:
    @staticmethod
    def create_schema(route: AppExceptionHandlingRoute):
        assert hasattr(route.endpoint, "_schema"), "Route does not have schema information."
        schema: ApiSchemaMap = cast(Any, {**route.endpoint._schema})
        schema["method"] = list(route.methods)[0]
        if isinstance(schema["permission"], Enum):
            schema["permission"] = schema["permission"].value
        schema["path"] = route.path
        schema["path_params"] = re_findall(PATH_PARAM_PATTERN, route.path)
        schema["description"] = route.description

        request_source_imports = ["from pydantic import BaseModel, Field"]
        request_source_others = {}
        request_source_model = ["class RequestForm(BaseModel):"]
        for path_param in schema["path_params"]:
            request_source_model.append(f"    {path_param}: str = Field(..., title='Path parameter: {path_param}')")

        if "query" in schema:
            if schema["query"]:
                schema["query"] = cast(BaseModel, schema["query"]).model_json_schema()
                imports, request_fields, other_classes = ApiSchemaHelper.__parse_model("query", schema["query"])
                request_source_imports.extend([imp for imp in imports if imp not in request_source_imports])
                request_source_model.extend(request_fields)
                request_source_others.update(other_classes)
            else:
                schema.pop("query")

        if "form" in schema:
            if schema["form"]:
                schema["form"] = cast(BaseModel, schema["form"]).model_json_schema()
                imports, request_fields, other_classes = ApiSchemaHelper.__parse_model("form", schema["form"])
                request_source_imports.extend([imp for imp in imports if imp not in request_source_imports])
                request_source_model.extend(request_fields)
                request_source_others.update(other_classes)
            else:
                schema.pop("form")

        content_type: str = "application/json"
        if "file_field" in schema:
            if schema["file_field"]:
                content_type = "multipart/form-data"
            else:
                schema.pop("file_field")
        schema["content_type"] = content_type

        if len(request_source_model) > 1:
            schema["request_schema_source"] = "\n".join(
                [
                    "\n".join(request_source_imports),
                    "\n".join(request_source_others.values()),
                    "\n".join(request_source_model),
                ]
            )

        collaborative_edit_targets = getattr(route.endpoint, "_collaborative_edit_targets", None)
        if collaborative_edit_targets:
            schema["collaborative_edit_targets"] = collaborative_edit_targets

        return schema

    @staticmethod
    def __parse_model(form_type: Literal["query", "form"], schema: Any):
        BASE_MODEL_CLASS_PATTERN = r"class (\w+)\(BaseModel\):"
        MODEL_FIELD_INDENT = 4

        parser = JsonSchemaParser(json_dumps(schema), use_subclass_enum=True, field_constraints=True)
        source: str = parser.parse()
        imports: list[str] = []
        other_classes: dict[str, str] = {}
        request_fields: list[str] = []

        started_type: Literal["other", "request"] | None = None
        other_chunks: list[str] = []
        base_model_occurrence = 0
        total_base_model_occurrence = len(re_findall(BASE_MODEL_CLASS_PATTERN, source))
        is_more_than_one_base_model = total_base_model_occurrence > 1

        for line in source.splitlines():
            if line.startswith("from"):
                if "__future__" not in line:
                    imports.append(line)
                continue

            if not line:
                if started_type == "other":
                    class_name, class_source = ApiSchemaHelper.__parse_other_class(other_chunks, imports)
                    other_classes[class_name] = class_source
                    other_chunks = []
                started_type = None
                continue

            if line.startswith("class"):
                started_type = "request" if "BaseModel" in line else "other"
                if started_type == "request":
                    if is_more_than_one_base_model and base_model_occurrence < total_base_model_occurrence - 1:
                        base_model_occurrence += 1
                        started_type = "other"

                if started_type == "other":
                    other_chunks = [line]
                continue

            if not started_type:
                continue

            if started_type == "request":
                old_line = line.strip()
                indent = len(line) - len(line.lstrip())
                is_model_field = (
                    indent == MODEL_FIELD_INDENT
                    and ":" in old_line
                    and not old_line.startswith(("class ", "def ", "@", "#"))
                    and not old_line.startswith("model_config")
                )
                if is_model_field:
                    new_line = f"{form_type}_{old_line}"
                    line = line.replace(old_line, new_line)
                    request_fields.append(line)
            else:
                other_chunks.append(line)

        if started_type == "other" and other_chunks:
            class_name, class_source = ApiSchemaHelper.__parse_other_class(other_chunks, imports)
            other_classes[class_name] = class_source

        return imports, request_fields, other_classes

    @staticmethod
    def __parse_other_class(other_chunks: list[str], imports: list[str]) -> tuple[str, str]:
        class_name = other_chunks[0].split("(")[0].split(" ")[-1]
        root_line = next((line for line in other_chunks if line.strip().startswith("__root__:")), None)
        if not root_line:
            return class_name, "\n".join(other_chunks)

        root_type = root_line.strip().removeprefix("__root__:").split(" = ", 1)[0].strip()
        other_chunks[0] = other_chunks[0].replace("(BaseModel):", f"(RootModel[{root_type}]):")
        root_line_index = other_chunks.index(root_line)
        other_chunks[root_line_index] = root_line.replace("__root__:", "root:")

        root_model_import = "from pydantic import RootModel"
        if root_model_import not in imports:
            imports.append(root_model_import)

        return class_name, "\n".join(other_chunks)
