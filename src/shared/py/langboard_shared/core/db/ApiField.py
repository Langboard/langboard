import json
from enum import Enum
from types import UnionType
from typing import Any, Callable, ClassVar, Literal, Union, _UnionGenericAlias, get_args, get_origin  # type: ignore
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from ..types import SafeDateTime, SnowflakeID
from ..utils.String import generate_random_string


_TConditions = dict[str, tuple[Literal["both", "schema", "api"], Any]]


class ApiField:
    __fields__: ClassVar[dict[str, "ApiField"]] = {}
    __api_field_cache: ClassVar[dict[type[BaseModel], tuple[tuple[str, "ApiField", FieldInfo], ...]]] = {}

    def __init__(
        self,
        *,
        name: str | None = None,
        schema_type: tuple[str, Literal["required", "optional"]] | None = None,
        converter: str | None = None,
        by_conditions: _TConditions | None = None,
        field_base_model: str | None = None,
    ):
        """Define an API field for a model attribute.

        :param name: The name to be used in the API response. If None, the attribute name will be used.
        :param converter: The name of a method in the model to convert the field value for the API response.
        :param by_conditions: A dictionary of conditions (key: kwargs name, value: expected) that must be matched exactly
                              (both in value and type) in the request for this field to be included.
        :param field_base_model: If the field's value is a Pydantic BaseModel, this specifies the model from which to extract and serialize the nested field.
        """
        self.__name = name
        self.__schema_type = schema_type
        self.__converter = converter
        self.__by_conditions = by_conditions
        self.__field_base_model = field_base_model

    @staticmethod
    def convert(model: BaseModel, **kwargs: Any) -> dict[str, Any]:
        api_result: dict[str, Any] = {}
        for field_name, api_field, _ in ApiField.__iter_api_fields(model):
            if not ApiField.__check_conditions(api_field.__by_conditions, ("both", "api"), **kwargs):
                continue

            value = getattr(model, field_name, None)
            api_name = api_field.__name if api_field.__name else field_name

            if api_field.__converter:
                converter: Callable[[], Any] | None = getattr(model, api_field.__converter, None)
                if not converter or not callable(converter):
                    raise ValueError(
                        f"Converter method '{api_field.__converter}' not found in model '{model.__class__.__name__}'"
                    )
                api_result[api_name] = converter()
                continue

            api_result[api_name] = ApiField.__get_default_converter(value, api_field.__field_base_model)
        return api_result

    @staticmethod
    def create_schema(model: type[BaseModel], **kwargs: Any) -> dict[str, Any]:
        api_schema: dict[str, Any] = {}
        for field_name, api_field, field_info in ApiField.__iter_api_fields(model):
            if (
                not ApiField.__check_conditions(api_field.__by_conditions, ("both", "schema"), **kwargs)
                or not field_info.annotation
            ):
                continue
            api_name = api_field.__name if api_field.__name else field_name
            if api_field.__schema_type:
                schema_name, requirement = api_field.__schema_type
            else:
                annotation = field_info.annotation
                schema_name, requirement = ApiField.__get_annotation_arg_schemas(api_field, annotation)
            if schema_name:
                if isinstance(schema_name, str):
                    api_schema[api_name] = f"{schema_name}{'' if requirement == 'required' else '?'}"
                elif isinstance(schema_name, (dict, list)):
                    api_schema[f"{api_name}{'' if requirement == 'required' else '?'}"] = schema_name

        return api_schema

    @staticmethod
    def __iter_api_fields(model: BaseModel | type[BaseModel]):
        model_cls = model if isinstance(model, type) else model.__class__
        api_fields = ApiField.__api_field_cache.get(model_cls)
        if api_fields is not None:
            yield from api_fields
            return

        api_fields = tuple(ApiField.__collect_api_fields(model_cls))
        ApiField.__api_field_cache[model_cls] = api_fields
        yield from api_fields

    @staticmethod
    def __collect_api_fields(model: type[BaseModel]):
        model_fields = model.model_fields
        for field, field_info in model_fields.items():
            api_field = next((metadata for metadata in field_info.metadata if isinstance(metadata, ApiField)), None)
            if api_field:
                yield field, api_field, field_info
                continue

            token = getattr(field_info, "_ApiField_token", None)
            if not token and isinstance(field_info.json_schema_extra, dict):
                token = field_info.json_schema_extra.get("_ApiField_token")
            if not token or token not in ApiField.__fields__:
                continue

            api_field = ApiField.__fields__[token]
            yield field, api_field, field_info

    @staticmethod
    def __get_annotation_arg_schemas(
        api_field: "ApiField", annotation: Any
    ) -> tuple[str | list | dict, Literal["required", "optional"]] | tuple[None, None]:
        is_union = (
            isinstance(annotation, UnionType)
            or isinstance(annotation, _UnionGenericAlias)
            or hasattr(annotation, "__origin__")
            and annotation.__origin__ is Union
        )
        is_optional = False
        schema_name: str | list | dict | None = None
        if is_union:
            schema_names, is_optional = ApiField.__get_iterable_arg_schema_names(api_field, annotation)
            if schema_names:
                schema_names = [name if isinstance(name, str) else json.dumps(name) for name in schema_names]
                schema_name = " | ".join(schema_names)
        elif isinstance(annotation, type):
            if issubclass(annotation, Enum):
                schema_name = f"Enum[{', '.join([enum_type.value for enum_type in annotation])}]"
            elif issubclass(annotation, BaseModel):
                if api_field.__field_base_model:
                    base_model_field: FieldInfo | None = annotation.model_fields.get(api_field.__field_base_model)
                    field_annotation = base_model_field.annotation if base_model_field else None
                    if field_annotation:
                        schema_name, _ = ApiField.__get_annotation_arg_schemas(api_field, field_annotation)
                else:
                    api_schema: Callable[[], str | dict] | None = getattr(annotation, "api_schema", None)
                    if api_schema and callable(api_schema):
                        schema_name = api_schema()
            elif annotation is SnowflakeID:
                schema_name = "string"
            elif annotation is SafeDateTime:
                schema_name = "string"

        if not schema_name:
            schema_name, is_optional = ApiField.__get_origin_name(api_field, annotation, is_optional)

        if not schema_name:
            return None, None
        return schema_name, "optional" if is_optional else "required"

    @staticmethod
    def __get_origin_name(
        api_field: "ApiField", annotation: Any, is_optional: bool
    ) -> tuple[str | list | dict | None, bool]:
        schema_name: str | list | dict | None = None
        origin = get_origin(annotation) or annotation
        if origin.__name__ == "str":
            schema_name = "string"
        elif origin.__name__ == "int":
            schema_name = "integer"
        elif origin.__name__ == "float":
            schema_name = "float"
        elif origin.__name__ == "bool":
            schema_name = "bool"
        elif origin.__name__ == "dict":
            schema_name = "object"
        elif origin.__name__ in ("list", "List"):
            schema_name, is_optional = ApiField.__get_iterable_arg_schema_names(api_field, annotation)
        return schema_name, is_optional

    @staticmethod
    def __get_iterable_arg_schema_names(api_field: "ApiField", annotation: Any) -> tuple[list[str | list | dict], bool]:
        args = get_args(annotation)
        schema_names: list[str | list | dict] = []
        is_optional = False
        for arg in args:
            if arg is type(None):
                is_optional = True
                continue
            arg_schema_name, _ = ApiField.__get_annotation_arg_schemas(api_field, arg)
            if arg_schema_name:
                schema_names.append(arg_schema_name)
        return schema_names, is_optional

    @staticmethod
    def __get_default_converter(value: Any, field_base_model: str | None = None) -> Any:
        if isinstance(value, list):
            return [ApiField.__get_default_converter(v, field_base_model) for v in value]
        if isinstance(value, SnowflakeID):
            return value.to_short_code()
        if isinstance(value, BaseModel):
            if field_base_model and hasattr(value, field_base_model):
                nested_value = getattr(value, field_base_model)
                return ApiField.__get_default_converter(nested_value)
            return value.model_dump()
        if isinstance(value, Enum):
            return value.value
        return value

    @staticmethod
    def __check_conditions(conditions: _TConditions | None, ranges: tuple[str, ...], **kwargs: Any) -> bool:
        if not conditions:
            return True

        for condition, (range, expected) in conditions.items():
            if range not in ranges:
                continue

            if condition not in kwargs:
                return False

            actual_value = kwargs[condition]
            if actual_value != expected or type(actual_value) is not type(expected):
                return False
        return True

    def assign_field(self, field: FieldInfo):
        token = generate_random_string(16)
        while token in ApiField.__fields__:
            token = generate_random_string(16)

        ApiField.__fields__[token] = self
        ApiField.__api_field_cache.clear()
        try:
            setattr(field, "_ApiField_token", token)
        except AttributeError:
            json_schema_extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
            json_schema_extra["_ApiField_token"] = token
            field.json_schema_extra = json_schema_extra
        return token

    def field_convert(self, model: BaseModel, field_name: str, value: Any, **kwargs: Any) -> dict[str, Any]:
        if not ApiField.__check_conditions(self.__by_conditions, ("both", "api"), **kwargs):
            return {}

        api_name = self.__name if self.__name else field_name
        converted_value = value
        converter: Callable[[], Any] | None = None
        if self.__converter:
            converter = getattr(model, self.__converter, None)
            if not converter or not callable(converter):
                raise ValueError(
                    f"Converter method '{self.__converter}' not found in model '{model.__class__.__name__}'"
                )
            converted_value = converter()
        else:
            converted_value = ApiField.__get_default_converter(value, self.__field_base_model)

        return {api_name: converted_value}
