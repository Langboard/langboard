from __future__ import annotations
from typing import ClassVar
from ..imports import IMPORT_TYPE_ALIAS, IMPORT_UNION, Import
from . import DataModel


class DataTypeUnion(DataModel):
    TEMPLATE_FILE_PATH: ClassVar[str] = "Union.jinja2"
    BASE_CLASS: ClassVar[str] = ""
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (
        IMPORT_TYPE_ALIAS,
        IMPORT_UNION,
    )
