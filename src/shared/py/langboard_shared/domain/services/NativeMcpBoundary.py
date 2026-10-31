"""Database boundary for native external OAuth calls, including indirect identifiers.

The context is installed only by the authenticated native transport. SQL predicates
are applied before counts, limits and projections, including direct SQLAlchemy
sessions used by domain services. Both ends of cross-board foreign keys intersect.
"""

from contextvars import ContextVar
from functools import lru_cache
from operator import add, sub
from sqlalchemy import and_, event, or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, ColumnClause, TextClause
from sqlalchemy.sql.selectable import Alias, Join, Select, TableClause


native_mcp_boundary = ContextVar("native_mcp_boundary", default=None)


class NativeMcpDenied(PermissionError):
    pass


@lru_cache(maxsize=None)
def _board_paths(table, seen=frozenset()):
    """Retain every board-bearing FK, excluding cycles and nullable absent links."""
    if table.name == "project":
        return True
    if table.name in seen:
        return ()
    paths = []
    # Retained history intentionally has no SQL FK so deletion preserves receipts.
    # Its native column contracts still identify the same current board resources.
    from ..models import Card, Project, ProjectColumn, ProjectWiki

    retained_targets = {
        "project_id": Project.__table__,
        "card_id": Card.__table__,
        "project_column_id": ProjectColumn.__table__,
        "project_wiki_id": ProjectWiki.__table__,
    }
    for name, target in retained_targets.items():
        if name in table.c and not table.c[name].foreign_keys and target.name not in seen | {table.name}:
            nested = _board_paths(target, seen | {table.name})
            if nested:
                paths.append((name, target, "id", nested))
    for column in table.c:
        for foreign in column.foreign_keys:
            target = foreign.column.table
            nested = _board_paths(target, seen | {table.name})
            if nested:
                paths.append((column.name, target, foreign.column.name, nested))
    return tuple(paths)


def _predicate(table, ids, paths=None):
    source = table.original if isinstance(table, Alias) and isinstance(table.original, TableClause) else table
    paths = _board_paths(source) if paths is None else paths
    if paths is True:
        return table.c.id.in_(ids)
    parts = []
    for name, target, target_key, nested in paths:
        condition = table.c[name].in_(select(target.c[target_key]).where(_predicate(target, ids, nested)))
        # Lightweight SQL projections have no nullable metadata. Require a
        # matching board reference rather than allowing an unknown NULL path.
        if getattr(source.c[name], "nullable", False):
            condition = or_(table.c[name].is_(None), condition)
        parts.append(condition)
    return and_(*parts) if parts else None


def _tables(from_clause):
    if isinstance(from_clause, Join):
        yield from _tables(from_clause.left)
        yield from _tables(from_clause.right)
    elif isinstance(from_clause, TableClause) or (
        isinstance(from_clause, Alias) and isinstance(from_clause.original, TableClause)
    ):
        yield from (from_clause,)


def _restricted_select(statement, ids):
    """Visit nested counts/subqueries and UNION branches before adding predicates."""

    def replace(node):
        if not isinstance(node, Select):
            return None
        # replacement_traverse stops below replacements; transform the original
        # children first, then append our own already-complete FK subqueries.
        cloned = visitors.cloned_traverse(node, {}, {"select": restrict})
        return cloned

    def restrict(node):
        predicates = []
        for from_clause in node.get_final_froms():
            for table in _tables(from_clause):
                condition = _predicate(table, ids)
                if condition is not None:
                    predicates.append(condition)
        if predicates:
            node._where_criteria += tuple(predicates)

    return visitors.replacement_traverse(statement, {}, replace)


def _values(statement, multiparams, params):
    if getattr(statement, "_multi_values", None):
        raise NativeMcpDenied("Native MCP cannot authorize literal multi-row inserts")
    if getattr(statement, "select", None) is not None:
        raise NativeMcpDenied("Native MCP cannot authorize INSERT FROM SELECT")
    if getattr(statement, "_post_values_clause", None) is not None:
        # Snowflake insert uses ON CONFLICT(id) DO NOTHING; conflict updates can
        # carry different FK destinations and need a separate reviewed contract.
        if "DO UPDATE" in str(statement._post_values_clause).upper():
            raise NativeMcpDenied("Native MCP cannot authorize conflict-update mutations")
    retained, bindings = {}, {}
    assignments = getattr(statement, "_ordered_values", None) or (getattr(statement, "_values", None) or {}).items()
    for column, value in assignments:
        name = column if isinstance(column, str) else column.name
        if not hasattr(value, "value"):
            # SQLite writer reservations retain each column's exact value.
            # This does not authorize expressions or assignments from other columns.
            if (
                isinstance(statement, Update)
                and isinstance(value, ColumnClause)
                and name in statement.table.c
                and value.compare(statement.table.c[name])
            ):
                continue
            # Native removal increments a keyword version and closes a card's
            # order gap. Only these fixed steps are safe; reference values stay literal.
            if (
                isinstance(statement, Update)
                and isinstance(value, BinaryExpression)
                and (statement.table.name, name, value.operator) in {
                    ("document_keyword_index", "version", add),
                    ("card", "order", add),
                    ("card", "order", sub),
                }
                and value.left.compare(statement.table.c[name])
                and isinstance(value.right, BindParameter)
                and type(value.right.value) is int
                and value.right.value == 1
            ):
                continue
            raise NativeMcpDenied("Native MCP cannot authorize expression-valued board mutations")
        if isinstance(value, BindParameter):
            if value.callable is not None:
                raise NativeMcpDenied("Native MCP cannot authorize deferred board mutation values")
            bindings[name] = value.key
        retained[name] = value.value
    payloads = list(multiparams) if multiparams else [params or {}]
    if any(not isinstance(payload, dict) for payload in payloads):
        raise NativeMcpDenied("Native MCP cannot authorize unclassified batch mutations")
    # Named execution parameters override bind defaults even when their keys
    # differ from target columns. Authorize the values SQLAlchemy will execute.
    return [
        {**retained, **payload, **{name: payload[key] for name, key in bindings.items() if key in payload}}
        for payload in payloads
    ]


class _InsertPayload(dict):
    """Distinguish omitted insert FKs from unchanged update FKs."""


def _check_references(connection, table, payload, ids, paths=None):
    paths = _board_paths(table) if paths is None else paths
    if paths is True:
        if "id" in payload and int(payload["id"]) not in ids:
            raise NativeMcpDenied("Board creation requires human consent after creation")
        return
    for name, target, target_key, nested in paths:
        if name not in payload:
            if isinstance(payload, _InsertPayload):
                raise NativeMcpDenied("Board foreign-key insert values must be explicit")
            continue
        value = payload[name]
        if value is None and table.c[name].nullable:
            continue
        condition = _predicate(target, ids, nested)
        if (
            connection.execute(select(target.c[target_key]).where(target.c[target_key] == value, condition)).first()
            is None
        ):
            raise NativeMcpDenied("Native MCP board reference is outside current consent")


def _current_scope(connection, scope):
    """Recheck revocation on the same primary connection before every statement.

    Intersect the invocation snapshot so new grants cannot expand an in-flight
    request. This check does not open or commit a separate transaction.
    """
    if "actor_id" not in scope:
        return scope  # Internal test harness scopes have no transport principal.
    from ..models import NativeMcpBoardConsent, NativeMcpConnection, Project, ProjectAssignedUser, ProjectRole, User

    user = User.__table__.c
    board = Project.__table__.c
    consent = NativeMcpBoardConsent.__table__.c
    client = NativeMcpConnection.__table__.c
    member = ProjectAssignedUser.__table__.c
    role = ProjectRole.__table__.c
    token = native_mcp_boundary.set(None)
    try:
        actor = connection.execute(
            select(user.id, user.activated_at, user.deleted_at, user.is_admin).where(user.id == scope["actor_id"])
        ).first()
        if actor is None or not actor.activated_at or actor.deleted_at:
            raise NativeMcpDenied("Current active native MCP user is required")
        rows = connection.execute(
            select(board.id, board.owner_id, member.id.label("member_id"), role.actions, consent.scopes)
            .select_from(NativeMcpBoardConsent.__table__)
            .join(NativeMcpConnection.__table__, client.id == consent.connection_id)
            .join(Project.__table__, board.id == consent.project_id)
            .outerjoin(ProjectAssignedUser.__table__, and_(member.project_id == board.id, member.user_id == actor.id))
            .outerjoin(ProjectRole.__table__, and_(role.project_id == board.id, role.user_id == actor.id))
            .where(client.id == scope["connection_id"], client.user_id == actor.id, board.deleted_at.is_(None))
        ).all()
        reads, writes = set(), set()
        for row in rows:
            if not row.member_id and row.owner_id != actor.id:
                continue
            actions = set(row.actions or [])
            owner = actor.is_admin or row.owner_id == actor.id
            read = owner or "*" in actions or "read" in actions
            write = (
                owner
                or "*" in actions
                or bool(actions.intersection({"update", "card_write", "card_update", "card_delete"}))
            )
            if read and "read" in row.scopes:
                reads.add(int(row.id))
            if read and write and "write" in row.scopes:
                writes.add(int(row.id))
        return {**scope, "read": scope["read"].intersection(reads), "write": scope["write"].intersection(writes)}
    finally:
        native_mcp_boundary.reset(token)


@event.listens_for(Engine, "before_execute", retval=True)
def _apply_native_boundary(connection, statement, multiparams, params, execution_options):
    scope = native_mcp_boundary.get()
    if scope is None:
        return statement, multiparams, params
    scope = _current_scope(connection, scope)
    if isinstance(statement, TextClause):
        raise NativeMcpDenied("Unattributed SQL is unavailable to external native MCP")
    if isinstance(statement, Select) or getattr(statement, "is_select", False):
        return _restricted_select(statement, scope["read"]), multiparams, params
    if isinstance(statement, (Insert, Update, Delete)):
        table = statement.table
        paths = _board_paths(table)
        if not paths and table.name not in {
            # Native board operations enqueue notifications and own activity;
            # invocation-level policy forbids reading this polymorphic history.
            "user_notification",
            "notification_email_delivery",
            "user_activity",
            "attachment_storage_object",
            "attachment_storage_reference",
            "document_vector_cleanup",
            "document_embedding_cache",
            "document_embedding_cache_reference",
            "document_extraction_cache",
            "document_extraction_cache_reference",
            "document_keyword_index",
        }:
            raise NativeMcpDenied("Instance-level mutation is unavailable to external native MCP")
        # Mutation predicates and RETURNING expressions can read other boards.
        # Restrict their nested SELECTs before adding the target's write scope.
        statement = _restricted_select(statement, scope["read"])
        if paths:
            ids = scope["write"]
            if not ids:
                raise NativeMcpDenied("Board write consent is required")
            # Reference checks use this same primary connection without recursion.
            token = native_mcp_boundary.set(None)
            try:
                if isinstance(statement, (Insert, Update)):
                    for payload in _values(statement, multiparams, params):
                        _check_references(
                            connection,
                            table,
                            _InsertPayload(payload) if isinstance(statement, Insert) else payload,
                            ids,
                            paths,
                        )
            finally:
                native_mcp_boundary.reset(token)
            if isinstance(statement, (Update, Delete)):
                statement = statement.where(_predicate(table, ids, paths))
        return statement, multiparams, params
    raise NativeMcpDenied("Unclassified database operation is unavailable to external native MCP")
