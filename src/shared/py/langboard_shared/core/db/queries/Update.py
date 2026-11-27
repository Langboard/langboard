from sqlalchemy import Update, update
from sqlalchemy.sql._typing import _DMLTableArgument
from ..Models import BaseDbModel, SoftDeleteModel


class UpdateQuery:
    def table(self, table: _DMLTableArgument, with_deleted: bool = False) -> Update:
        if not isinstance(table, type) or not issubclass(table, BaseDbModel):
            raise ValueError("Table must be a subclass of BaseDbModel")

        statement = update(table)
        if not with_deleted and (isinstance(table, type) and issubclass(table, SoftDeleteModel)):
            statement = statement.where(table.column("deleted_at") == None)  # noqa
        return statement
