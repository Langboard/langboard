from sqlalchemy import Insert, insert
from sqlalchemy.sql._typing import _DMLTableArgument
from ..Models import BaseDbModel


class InsertQuery:
    def table(self, table: _DMLTableArgument) -> Insert:
        if not isinstance(table, type) or not issubclass(table, BaseDbModel):
            raise ValueError("Table must be a subclass of BaseDbModel")

        return insert(table)
