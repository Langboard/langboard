from sqlalchemy import Delete, delete
from sqlalchemy.sql._typing import _DMLTableArgument
from ..Models import BaseDbModel


class DeleteQuery:
    def table(self, table: _DMLTableArgument) -> Delete:
        if not isinstance(table, type) or not issubclass(table, BaseDbModel):
            raise ValueError("Table must be a subclass of BaseDbModel")

        return delete(table)
