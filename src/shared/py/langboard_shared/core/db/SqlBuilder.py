from ..utils.decorators import class_instance, singleton
from .queries import DeleteQuery, InsertQuery, SelectQuery, UpdateQuery


@class_instance()
@singleton
class SqlBuilder:
    """Provides methods for building SQL statements using SQLAlchemy functions.

    The :meth:`update` and :meth:`select` functions include `soft delete functionality`.

    The :meth:`delete` function applies the functionality when executing the statement.
    """

    @property
    def insert(self) -> InsertQuery:
        return InsertQuery()

    @property
    def update(self) -> UpdateQuery:
        return UpdateQuery()

    @property
    def delete(self) -> DeleteQuery:
        return DeleteQuery()

    @property
    def select(self) -> SelectQuery:
        return SelectQuery()
