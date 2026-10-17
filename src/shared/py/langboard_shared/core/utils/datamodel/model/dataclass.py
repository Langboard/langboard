from __future__ import annotations
from typing import TYPE_CHECKING, Any, ClassVar, Optional
from ..imports import (
    Import,
)
from ..types import chain_as_tuple
from . import DataModel, DataModelFieldBase
from .base import UNDEFINED
from .imports import IMPORT_DATACLASS, IMPORT_FIELD
from .types import DateTimeDataTypeManager


if TYPE_CHECKING:
    from collections import defaultdict
    from pathlib import Path
    from ..reference import Reference

from .pydantic.base_model import Constraints  # noqa: TC001


def _has_field_assignment(field: DataModelFieldBase) -> bool:
    return bool(field.field) or not (
        field.required or (field.represented_default == "None" and field.strip_default_none)
    )


class DataClass(DataModel):
    TEMPLATE_FILE_PATH: ClassVar[str] = "dataclass.jinja2"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_DATACLASS,)

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
    ) -> None:
        super().__init__(
            reference=reference,
            fields=sorted(fields, key=_has_field_assignment),
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
        )


class DataModelField(DataModelFieldBase):
    _FIELD_KEYS: ClassVar[set[str]] = {
        "default_factory",
        "init",
        "repr",
        "hash",
        "compare",
        "metadata",
        "kw_only",
    }
    constraints: Optional[Constraints] = None  # noqa: UP045

    @property
    def imports(self) -> tuple[Import, ...]:
        field = self.field
        if field and field.startswith("field("):
            return chain_as_tuple(super().imports, (IMPORT_FIELD,))
        return super().imports

    def self_reference(self) -> bool:  # pragma: no cover
        return isinstance(self.parent, DataClass) and self.parent.reference.path in {
            d.reference.path for d in self.data_type.all_data_types if d.reference
        }

    @property
    def field(self) -> str | None:
        """for backwards compatibility"""
        result = str(self)
        if not result:
            return None
        return result

    def __str__(self) -> str:
        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k in self._FIELD_KEYS}

        if self.default != UNDEFINED and self.default is not None:
            data["default"] = self.default

        if self.required:
            data = {
                k: v
                for k, v in data.items()
                if k
                not in {
                    "default",
                    "default_factory",
                }
            }

        if not data:
            return ""

        if len(data) == 1 and "default" in data:
            default = data["default"]

            if isinstance(default, (list, dict)):
                return f"field(default_factory=lambda :{default!r})"
            return repr(default)
        kwargs = [f"{k}={v if k == 'default_factory' else repr(v)}" for k, v in data.items()]
        return f"field({', '.join(kwargs)})"


class DataTypeManager(DateTimeDataTypeManager):
    pass
