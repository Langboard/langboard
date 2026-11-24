"""Explicit module composition. Grouping never changes module authority."""

import re
from ...core.db import DbSession, SqlBuilder
from ...helpers import InfraHelper
from ...publishers import AppSettingPublisher
from ..models import AppComposition, AppCompositionMember, AppDefinition, BoardAppBinding, Project
from ..models.ProjectRole import ProjectRoleAction
from .AppManifest import APP_MANIFESTS
from .AppRegistry import AppRegistryConflict, AppRegistryDenied, _admin


def _members(db, row, *, lock=False):
    query = SqlBuilder.select.table(AppCompositionMember).where(
        AppCompositionMember.composition_id == row.id).order_by(AppCompositionMember.order)
    return db.exec(query.with_for_update() if lock else query).all()


def _list(db):
    rows = db.exec(SqlBuilder.select.table(AppComposition).order_by(AppComposition.key)).all()
    members = db.exec(SqlBuilder.select.table(AppCompositionMember).order_by(AppCompositionMember.order)).all()
    by_id = {}
    for member in members:
        by_id.setdefault(member.composition_id, []).append(member.module_key)
    return [row.response(by_id.get(row.id, [])) for row in rows]


def list_compositions(actor):
    with DbSession.atomic() as db:
        _admin(db, actor)
        return _list(db)


def composition_catalog(actor):
    """Administrator discovery uses exact installed keys, never provider-name guesses."""
    with DbSession.atomic() as db:
        _admin(db, actor)
        modules = [{"key": key, "name": manifest.name, "version": manifest.version,
                    "is_enabled": True, "builtin": True} for key, manifest in APP_MANIFESTS.items()]
        for row in db.exec(SqlBuilder.select.table(AppDefinition).order_by(AppDefinition.key)).all():
            modules.append({"key": row.key, "name": row.declaration.get("name", row.key),
                            "version": row.declaration.get("version"), "is_enabled": row.is_enabled,
                            "builtin": False})
        return {"compositions": _list(db), "modules": modules}


def board_compositions(service, actor, project_uid):
    with DbSession.atomic() as db:
        if service._authorized_app_board(actor, project_uid, ProjectRoleAction.Read, revocation=True) is None:
            raise AppRegistryDenied()
        return {"compositions": _list(db), "apps": service.get_app_catalog(actor, project_uid)}


def save_composition(actor, key, name, description, module_keys, expected_revision=None):
    if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", key):
        raise ValueError("Invalid composition key")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        raise ValueError("Composition needs bounded fallback name")
    if not isinstance(description, str) or not description.strip() or len(description) > 1000:
        raise ValueError("Composition needs bounded fallback description")
    if (not isinstance(module_keys, list) or not 1 <= len(module_keys) <= 16
        or any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", k) for k in module_keys)
        or len(set(module_keys)) != len(module_keys)):
        raise ValueError("Composition needs unique bounded module keys")
    with DbSession.atomic() as db:
        _admin(db, actor)
        definitions = db.exec(SqlBuilder.select.table(AppDefinition).order_by(AppDefinition.key).with_for_update()).all()
        known = set(APP_MANIFESTS) | {row.key for row in definitions}
        if not set(module_keys).issubset(known):
            raise ValueError("Composition members must be existing app modules")
        row = db.exec(SqlBuilder.select.table(AppComposition).where(AppComposition.key == key).with_for_update()).first()
        if row is None:
            if expected_revision is not None:
                raise AppRegistryConflict()
            row = AppComposition(key=key, name=name, description=description, approved_by=actor.id)
            db.insert(row)
            existing = []
        else:
            existing = _members(db, row, lock=True)
            if row.response([m.module_key for m in existing])["revision"] != expected_revision:
                raise AppRegistryConflict()
        memberships = db.exec(SqlBuilder.select.table(AppCompositionMember).where(
            AppCompositionMember.module_key.in_(module_keys)).with_for_update()).all()
        if any(member.composition_id != row.id for member in memberships):
            raise AppRegistryConflict()
        keys = [m.module_key for m in existing]
        if keys == module_keys and row.name == name and row.description == description:
            return row.response(keys)
        for member in existing:
            db.delete(member)
        for order, module_key in enumerate(module_keys):
            db.insert(AppCompositionMember(composition_id=row.id, module_key=module_key, order=order))
        if existing:
            row.generation += 1
        row.name, row.description, row.approved_by = name, description, actor.id
        db.update(row)
        db.after_commit(AppSettingPublisher.apps_changed)
        return row.response(module_keys)


def ungroup_composition(actor, key, expected_revision):
    with DbSession.atomic() as db:
        _admin(db, actor)
        row = db.exec(SqlBuilder.select.table(AppComposition).where(AppComposition.key == key).with_for_update()).first()
        if row is None:
            return None
        members = _members(db, row, lock=True)
        if row.response([m.module_key for m in members])["revision"] != expected_revision:
            raise AppRegistryConflict()
        for member in members:
            db.delete(member)
        db.delete(row)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {"key": key, "ungrouped": True}


def disable_board_composition(service, actor, project_uid, key, expected_revision, binding_revisions):
    """Withdraw all current member grants atomically, retaining durable records."""
    with DbSession.atomic() as db:
        board = db.exec(SqlBuilder.select.table(Project).where(
            Project.id == InfraHelper.convert_id(project_uid)).with_for_update()).first()
        if board is None or service._authorized_app_board(
            actor, board.id, ProjectRoleAction.Update, lock=True, revocation=True) is None:
            raise AppRegistryDenied()
        row = db.exec(SqlBuilder.select.table(AppComposition).where(
            AppComposition.key == key).with_for_update()).first()
        if row is None:
            raise AppRegistryConflict()
        keys = [member.module_key for member in _members(db, row, lock=True)]
        if row.response(keys)["revision"] != expected_revision or set(binding_revisions) != set(keys):
            raise AppRegistryConflict()
        bindings = db.exec(SqlBuilder.select.table(BoardAppBinding).where(
            BoardAppBinding.project_id == board.id, BoardAppBinding.app_key.in_(keys))
            .order_by(BoardAppBinding.app_key).with_for_update()).all()
        by_key = {binding.app_key: binding for binding in bindings}
        for module_key in keys:
            binding = by_key.get(module_key)
            revision = binding.edit_revision() if binding else None
            if binding_revisions[module_key] != revision:
                raise AppRegistryConflict()
        for binding in bindings:
            service._disable_locked_app_binding(db, binding)
        db.after_commit(AppSettingPublisher.apps_changed)
        return {"key": key, "disabled": True, "module_keys": keys}
