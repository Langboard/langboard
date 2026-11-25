"""User discovery is a bounded projection, never an installation or connection grant."""

import re
from sqlalchemy import and_, literal, or_, select, union_all
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppComposition, AppCompositionMember, AppDefinition
from ..models.ProjectRole import ProjectRoleAction
from .AppGovernance import _actor, _authorized_board, current_policy
from .AppManifest import APP_MANIFESTS
from .AppPackage import APP_STORE_CATEGORIES


def list_catalog(
    actor, *, project_uid=None, after_key=None, limit=25, app_key=None, composition_key=None,
    include_compositions=False, featured_keys=(), query=None, category=None, catalog_categories=None,
):
    if type(include_compositions) is not bool:
        raise ValueError("Invalid composition projection selector")
    logical_mode = include_compositions or composition_key is not None
    cursor_pattern = r"[a-z][a-z0-9_-]{0,31}" + (r"(?:~(?:composition|module))?" if logical_mode else "")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Catalog page limit must be 1 to 50")
    if after_key is not None and not re.fullmatch(cursor_pattern, after_key):
        raise ValueError("Invalid catalog cursor")
    if app_key is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", app_key):
        raise ValueError("Invalid app key")
    if composition_key is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", composition_key):
        raise ValueError("Invalid composition key")
    if app_key is not None and composition_key is not None:
        raise ValueError("Select a module or composition")
    if (app_key is not None or composition_key is not None) and after_key is not None:
        raise ValueError("App detail cannot use a page cursor")
    if len(featured_keys) > 16:
        raise ValueError("Too many featured apps")
    if query is not None and (not isinstance(query, str) or len(query) > 120):
        raise ValueError("Invalid catalog search")
    if category is not None and category not in APP_STORE_CATEGORIES:
        raise ValueError("Invalid catalog category")
    if app_key is not None or composition_key is not None:
        category = None
    query = (query or "").strip() if app_key is None and composition_key is None else ""
    catalog_categories = catalog_categories or {}
    project_id = None
    if project_uid is not None:
        if not isinstance(project_uid, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", project_uid):
            raise ValueError("Invalid project identifier")
        project_id = SnowflakeID.from_short_code(project_uid)
        if not project_id or project_id.to_short_code() != project_uid:
            raise ValueError("Invalid project identifier")
    with DbSession.atomic() as db:
        actor = _actor(db, actor, for_update=False)
        organization_id = None
        if project_id is not None:
            project = _authorized_board(db, actor, project_id, for_update=False, action=ProjectRoleAction.Read)
            organization_id = project.organization_id
        policy = current_policy(db, organization_id)
        result = {"items": [], "featured": [], "next_cursor": None, "effective_mode": policy["effective_mode"],
                  "can_manage_installation": actor.is_admin}
        if policy["effective_mode"] == "disabled":
            return result
        # Page logical identities in SQL before fetching their bounded module projections.
        # Exact legacy module detail deliberately bypasses presentation membership.
        if not logical_mode:
            statement = SqlBuilder.select.table(AppDefinition).where(AppDefinition.is_enabled.is_(True))
            if app_key is not None:
                statement = statement.where(AppDefinition.key == app_key)
            if query:
                statement = statement.where(_module_matches(query))
            if category:
                statement = statement.where(_module_category(category, catalog_categories))
            if after_key is not None:
                statement = statement.where(AppDefinition.key > after_key)
            rows = db.exec(statement.order_by(AppDefinition.key).limit(limit + 1)).all()
            page = rows[:limit]
            result["items"] = [_entry(row, catalog_categories) for row in page]
            result["next_cursor"] = page[-1].key if len(rows) > limit else None
        elif app_key is not None:
            rows = db.exec(SqlBuilder.select.table(AppDefinition).where(
                AppDefinition.is_enabled.is_(True), AppDefinition.key == app_key).limit(1)).all()
            result["items"] = [_entry(row, catalog_categories) for row in rows]
        else:
            membership = select(AppCompositionMember.id).where(
                AppCompositionMember.module_key == AppDefinition.key).exists()
            modules = select(AppDefinition.key.label("key"), literal("module").label("kind")).where(
                AppDefinition.is_enabled.is_(True), ~membership
            )
            compositions = select(AppComposition.key.label("key"), literal("composition").label("kind"))
            if query:
                modules = modules.where(_module_matches(query))
                compositions = compositions.where(or_(
                    AppComposition.key.icontains(query, autoescape=True),
                    AppComposition.name.icontains(query, autoescape=True),
                    AppComposition.description.icontains(query, autoescape=True),
                ))
            if category:
                modules = modules.where(_module_category(category, catalog_categories))
                compositions = compositions.where(_composition_category(category, catalog_categories))
            logical = union_all(modules, compositions).subquery()
            statement = select(logical.c.key, logical.c.kind)
            if composition_key is not None:
                statement = statement.where(logical.c.key == composition_key, logical.c.kind == "composition")
            if after_key is not None:
                cursor_key, _, cursor_kind = after_key.partition("~")
                boundary = logical.c.key > cursor_key
                if cursor_kind:
                    boundary = or_(boundary, and_(logical.c.key == cursor_key, logical.c.kind > cursor_kind))
                statement = statement.where(boundary)
            rows = db.exec(statement.order_by(logical.c.key, logical.c.kind).limit(limit + 1)).all()
            page = rows[:limit]
            result["items"] = _logical_entries(db, page, catalog_categories)
            if len(rows) > limit:
                result["next_cursor"] = f"{page[-1][0]}~{page[-1][1]}"
        if featured_keys and not logical_mode:
            featured = db.exec(SqlBuilder.select.table(AppDefinition).where(
                AppDefinition.is_enabled.is_(True), AppDefinition.key.in_(featured_keys)).limit(16)).all()
            result["featured"] = [_entry(row, catalog_categories) for row in featured]
        elif featured_keys:
            # A featured module resolves to its explicit logical identity, including
            # groups whose other members sort outside this page. Never infer a vendor.
            memberships = db.exec(SqlBuilder.select.table(AppCompositionMember).where(
                AppCompositionMember.module_key.in_(featured_keys)).limit(16)).all()
            grouped = {member.module_key for member in memberships}
            group_ids = {member.composition_id for member in memberships}
            groups = db.exec(SqlBuilder.select.table(AppComposition).where(
                AppComposition.id.in_(group_ids)).order_by(AppComposition.key).limit(16)).all() if group_ids else []
            modules = db.exec(SqlBuilder.select.table(AppDefinition).where(
                AppDefinition.is_enabled.is_(True), AppDefinition.key.in_(set(featured_keys) - grouped))
                .order_by(AppDefinition.key).limit(16)).all()
            grouped_entries = _logical_entries(db, [(row.key, "composition") for row in groups], catalog_categories)
            result["featured"] = [*grouped_entries, *[_entry(row, catalog_categories) for row in modules]]
        return result


def _module_matches(query: str):
    return or_(
        AppDefinition.key.icontains(query, autoescape=True),
        AppDefinition.declaration["name"].as_string().icontains(query, autoescape=True),
        AppDefinition.declaration["description"].as_string().icontains(query, autoescape=True),
    )


def _module_category(category, catalog_categories):
    stored = AppDefinition.package["store"]["category"].as_string()
    official_keys = [key for key, value in catalog_categories.items() if value == category]
    return or_(AppDefinition.key.in_(official_keys), and_(~AppDefinition.key.in_(tuple(catalog_categories)), stored == category))


def _composition_category(category, catalog_categories):
    builtin_keys = [key for key in APP_MANIFESTS if catalog_categories.get(key) == category]
    return (
        select(AppCompositionMember.id)
        .outerjoin(AppDefinition, AppDefinition.key == AppCompositionMember.module_key)
        .where(
            AppCompositionMember.composition_id == AppComposition.id,
            or_(
                and_(AppDefinition.is_enabled.is_(True), _module_category(category, catalog_categories)),
                and_(AppDefinition.id.is_(None), AppCompositionMember.module_key.in_(builtin_keys)),
            ),
        )
        .exists()
    )


def _category_fields(category):
    return {"categories": [category]} if category in APP_STORE_CATEGORIES else {}


def _logical_entries(db, identities, catalog_categories):
    group_keys = [key for key, kind in identities if kind == "composition"]
    module_keys = [key for key, kind in identities if kind == "module"]
    groups = db.exec(SqlBuilder.select.table(AppComposition).where(
        AppComposition.key.in_(group_keys)).limit(50)).all() if group_keys else []
    by_id = {row.id: row for row in groups}
    members = db.exec(SqlBuilder.select.table(AppCompositionMember).where(
        AppCompositionMember.composition_id.in_(by_id)).order_by(
            AppCompositionMember.composition_id, AppCompositionMember.order).limit(50 * 16)).all() if by_id else []
    keys = set(module_keys) | {member.module_key for member in members}
    definitions = db.exec(SqlBuilder.select.table(AppDefinition).where(
        AppDefinition.key.in_(keys)).limit(50 * 16 + 50)).all() if keys else []
    definitions = {row.key: row for row in definitions}
    grouped = {}
    for member in members:
        grouped.setdefault(member.composition_id, []).append(member.module_key)
    entries = {}
    for row in groups:
        keys = grouped.get(row.id, [])
        modules = []
        for key in keys:
            if key in definitions:
                definition = definitions[key]
                modules.append({**_entry(definition, catalog_categories), "is_enabled": definition.is_enabled,
                                "revision": definition.edit_revision(), "builtin": False})
            elif key in APP_MANIFESTS:
                manifest = APP_MANIFESTS[key]
                modules.append({"key": key, "name": manifest.name, "description": manifest.description,
                                "version": manifest.version, "capabilities": list(manifest.capabilities),
                                "resource_types": list(manifest.resource_types), "is_installed": True,
                                "is_enabled": True, "builtin": True, **_category_fields(catalog_categories.get(key))})
        entry = {
            **row.response(keys), "kind": "composition", "composition_key": row.key,
            "modules": modules, "is_installed": bool(modules),
        }
        categories = {category for module in modules if module["is_enabled"] for category in module.get("categories", ())}
        if categories:
            entry["categories"] = sorted(categories)
        entries[(row.key, "composition")] = entry
    for key in module_keys:
        if key in definitions:
            entries[(key, "module")] = _entry(definitions[key], catalog_categories)
    return [entries[(key, kind)] for key, kind in identities if (key, kind) in entries]


def _entry(row, catalog_categories):
    package = row.package or {}
    store = package.get("store") or {}
    source = package.get("kind") if package.get("kind") in ("official", "manual") else "registered"
    installation = None
    if source != "registered" and re.fullmatch(r"[0-9a-f]{64}", package.get("sha256", "")):
        installation = {"kind": source, "sha256": package["sha256"],
                        "store": {key: store[key] for key in ("icon", "icon_dark", "screenshots") if key in store}}
    return {"key": row.key, "name": row.declaration["name"], "version": row.declaration["version"],
            "description": row.declaration["description"], "capabilities": row.declaration["capabilities"],
            "resource_types": row.declaration["resource_types"], "is_installed": True,
            "source": source, "installation": installation,
            **_category_fields(catalog_categories.get(row.key) or store.get("category"))}
