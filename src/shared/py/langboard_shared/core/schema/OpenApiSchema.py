from enum import Enum
from typing import Any, Literal, Self, cast
from fastapi import status
from fastapi.openapi.constants import REF_PREFIX
from ..db import BaseDbModel
from ..routing import ApiErrorCode


class OpenApiSchema:
    def __init__(self, success_code: Literal[200, 201, 202, 204] | None = 200) -> None:
        self.__schema: dict[int | str, dict[str, Any]] = {}
        self.__errors: list[tuple[int, ApiErrorCode | str | tuple[str, dict]]] = [
            (
                status.HTTP_400_BAD_REQUEST,
                (
                    "Invalid request.",
                    {
                        "ref": "ValidationError",
                        "example": {
                            "code": ApiErrorCode.VA0000.name,
                            "message": ApiErrorCode.VA0000.value,
                            "errors": {
                                "Enum[body, query, path, header]": {
                                    "location": ["<field name>"],
                                },
                            },
                        },
                    },
                ),
            ),
        ]

        if success_code:
            self.__schema[success_code] = {
                "description": "Successful response.",
                "content": self.__empty_schema(),
            }

    def suc(self, schema: Any, status_code: Literal[200, 201, 202, 204] = 200) -> Self:
        if status_code != 200:
            self.__schema.pop(status.HTTP_200_OK, None)

        schema = self.__make_schema_recursive(schema)

        self.__schema[status_code] = {
            "description": "Successful response.",
            "content": {"application/json": {"example": schema}},
        }
        return self

    def err(self, status_code: int, *error_codes: ApiErrorCode) -> Self:
        for error_code in error_codes:
            self.__errors.append((status_code, error_code))
        return self

    def auth(self, only_bot: bool = False) -> Self:
        self.__errors.append((status.HTTP_401_UNAUTHORIZED, "Authentication token is invalid."))
        if not only_bot:
            self.__errors.append((status.HTTP_422_UNPROCESSABLE_CONTENT, "Authentication token is expired."))
        return self

    def forbidden(self) -> Self:
        self.__errors.append((status.HTTP_403_FORBIDDEN, ApiErrorCode.PE1001))
        return self

    def get(self) -> dict[int | str, dict[str, Any]]:
        self.__set_errors()
        schema = self.__schema
        self.__schema = {}
        return schema

    def __empty_schema(self) -> dict[str, Any]:
        return {"application/json": {"schema": {"$ref": f"{REF_PREFIX}EmptyResponse"}}}

    def __make_schema_recursive(self, value: Any) -> Any:
        if isinstance(value, type) and issubclass(value, Enum):
            return f"Enum[{', '.join([enum_value.value for enum_value in value])}]"

        if (isinstance(value, type) and issubclass(value, BaseDbModel)) or hasattr(value, "api_schema"):
            return self.__make_schema_recursive(value.api_schema())

        if isinstance(value, dict):
            new_dict: dict[str, Any] = {}
            for key, dict_value in value.items():
                if isinstance(key, type) and issubclass(key, Enum):
                    new_dict[self.__make_schema_recursive(key)] = self.__make_schema_recursive(dict_value)
                else:
                    new_dict[cast(str, key)] = self.__make_schema_recursive(dict_value)
            return new_dict

        if isinstance(value, tuple):
            if len(value) == 2 and isinstance(value[1], (dict, tuple)):
                if (isinstance(value[0], type) and issubclass(value[0], BaseDbModel)) or hasattr(
                    value[0], "api_schema"
                ):
                    if isinstance(value[1], tuple):
                        return self.__make_schema_recursive(value[0].api_schema(*value[1]))
                    else:
                        return self.__make_schema_recursive(value[0].api_schema(**value[1]))
                elif isinstance(value[0], (tuple, list)):
                    merged_schema = {}
                    for item in value[0]:
                        if not (isinstance(item, type) and issubclass(item, BaseDbModel)) and not hasattr(
                            item, "api_schema"
                        ):
                            continue

                        if isinstance(value[1], tuple):
                            merged_schema.update(self.__make_schema_recursive(item.api_schema(*value[1])))
                        else:
                            merged_schema.update(self.__make_schema_recursive(item.api_schema(**value[1])))
                    return merged_schema
            else:
                merged_schema = {}
                for item in value:
                    if not (isinstance(item, type) and issubclass(item, BaseDbModel)) and not hasattr(
                        item, "api_schema"
                    ):
                        continue
                    merged_schema.update(self.__make_schema_recursive(item.api_schema()))
                return merged_schema

        if isinstance(value, list):
            new_list: list = []
            for list_value in value:
                new_list.append(self.__make_schema_recursive(list_value))
            return new_list

        return value

    def __set_errors(self) -> None:
        description_lines_map: dict[int, list[str]] = {}
        refs_map: dict[int, list[str]] = {}
        examples_map: dict[int, dict] = {}

        for status_code, error in self.__errors:
            if status_code not in description_lines_map:
                description_lines_map[status_code] = []
            if status_code not in refs_map:
                refs_map[status_code] = []
            if status_code not in examples_map:
                examples_map[status_code] = {}

            description_lines = description_lines_map[status_code]
            refs = refs_map[status_code]
            examples = examples_map[status_code]

            if isinstance(error, ApiErrorCode):
                description_lines.append(f"{error.name}: {error.value}")
                refs.append(f"{REF_PREFIX}ApiErrorCode{error.name}")
                examples[error.name] = {"code": error.name, "message": error.value}
            elif isinstance(error, tuple):
                description, ref_map = error
                ref = ref_map["ref"]
                example = ref_map.get("example", {})
                description_lines.append(description)
                refs.append(f"{REF_PREFIX}{ref}")
                examples[ref] = example
            else:
                description_lines.append(error)
                refs.append(f"{REF_PREFIX}EmptyResponse")
                examples[error] = {"EmptyResponse": {}}

        if len(description_lines_map) != len(refs_map) or len(description_lines_map) != len(examples_map):
            raise ValueError("Mismatch in status codes among description_lines_map, refs_map, and examples_map.")

        for status_code, description_lines in description_lines_map.items():
            if status_code not in refs_map or status_code not in examples_map:
                raise ValueError(f"Status code {status_code} is missing in refs_map or examples_map.")

            refs = refs_map[status_code]
            examples = examples_map[status_code]

            if len(refs) == 1:
                schema = {"$ref": refs[0]}
                examples = {"example": examples[list(examples.keys())[0]]}
            else:
                schema = {
                    "oneOf": [{"$ref": ref} for ref in refs],
                }
                examples = {"examples": examples}

            self.__schema[status_code] = {
                "description": "\n\n".join(description_lines),
                "content": {"application/json": {"schema": schema, **examples}},
            }
