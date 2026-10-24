"""Current authority and durable exact-artifact installation request receipts."""

import hashlib
import json
import re
from sqlalchemy import update
from ...core.db import DbSession, SqlBuilder
from ...core.types import SnowflakeID
from ..models import AppInstallationRequest, User
from .AppGovernance import AppGovernanceDenied, _actor, _authorized_board, current_policy, lock_policy_mutation
from .AppRegistry import AppRegistryConflict, AppRegistryDenied, _admin


def _uid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{11}", value):
        raise ValueError("Invalid request identifier")
    result = SnowflakeID.from_short_code(value)
    if result.to_short_code() != value:
        raise ValueError("Invalid request identifier")
    return result


def authorize_submission(db, actor, project_uid=None):
    lock_policy_mutation(db)
    actor = _actor(db, actor)
    project = _authorized_board(db, actor, _uid(project_uid)) if project_uid else None
    if current_policy(db, project.organization_id if project else None)["effective_mode"] == "disabled":
        raise AppGovernanceDenied()
    return actor, project


def identity(request_key, selector, expected_revision, project_uid):
    if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", request_key):
        raise ValueError("Invalid request key")
    if expected_revision is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
        raise ValueError("Invalid app revision")
    return hashlib.sha256(
        json.dumps([selector, expected_revision, project_uid], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def existing_request(db, actor, request_key, fingerprint):
    row = db.exec(
        SqlBuilder.select.table(AppInstallationRequest)
        .where(AppInstallationRequest.requester_id == actor.id, AppInstallationRequest.request_key == request_key)
        .with_for_update()
    ).first()
    if row and row.fingerprint != fingerprint:
        raise AppRegistryConflict()
    return row


def list_requests(actor, *, after=None, limit=25):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("Invalid page size")
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        query = SqlBuilder.select.table(AppInstallationRequest)
        if not actor.is_admin:
            query = query.where(AppInstallationRequest.requester_id == actor.id)
        if after:
            query = query.where(AppInstallationRequest.id > _uid(after))
        rows = db.exec(query.order_by(AppInstallationRequest.id).limit(limit + 1)).all()
        page = rows[:limit]
        return {
            "items": [row.response() for row in page],
            "next_cursor": page[-1].get_uid() if len(rows) > limit else None,
        }


def get_request(actor, uid):
    with DbSession.atomic() as db:
        actor = _actor(db, actor)
        row = db.exec(
            SqlBuilder.select.table(AppInstallationRequest).where(AppInstallationRequest.id == _uid(uid))
        ).first()
        if row is None or (not actor.is_admin and row.requester_id != actor.id):
            raise AppRegistryDenied()
        return row.response()


def review_request(actor, uid, decision, expected_revision, reason, installer, prepare):
    if decision not in ("approve", "reject") or not isinstance(reason, str) or len(reason) > 1000:
        raise ValueError("Invalid review")
    prepared, preparation_error = None, None
    if decision == "approve":
        with DbSession.atomic() as db:
            lock_policy_mutation(db)
            _admin(db, actor)
            preview = db.exec(
                SqlBuilder.select.table(AppInstallationRequest).where(AppInstallationRequest.id == _uid(uid))
            ).first()
            if preview is None:
                raise AppRegistryDenied()
            pending = preview.decision == "pending"
            if pending:
                if preview.edit_revision() != expected_revision:
                    raise AppRegistryConflict()
                requester = db.exec(SqlBuilder.select.table(User).where(User.id == preview.requester_id)).first()
                if requester is None:
                    raise AppGovernanceDenied()
                authorize_submission(db, requester, preview.project_id.to_short_code() if preview.project_id else None)
                artifact, app_revision = preview.artifact, preview.expected_app_revision
        if pending:
            try:
                prepared = prepare(actor, artifact, app_revision)
            except (ValueError, AppRegistryConflict) as exc:
                preparation_error = exc
    with DbSession.atomic() as db:
        lock_policy_mutation(db)
        _admin(db, actor)
        row = db.exec(
            SqlBuilder.select.table(AppInstallationRequest)
            .where(AppInstallationRequest.id == _uid(uid))
            .with_for_update()
        ).first()
        if row is None:
            raise AppRegistryDenied()
        desired = "approved" if decision == "approve" else "rejected"
        if row.decision != "pending":
            # The original revision is valid only for the identical completed review.
            original = hashlib.sha256(
                json.dumps([row.get_uid(), row.fingerprint, 1], separators=(",", ":")).encode()
            ).hexdigest()
            if (
                row.decision != desired
                or row.reviewer_id != actor.id
                or row.reason != reason
                or expected_revision not in (original, row.edit_revision())
            ):
                raise AppRegistryConflict()
            return row.response()
        if row.edit_revision() != expected_revision:
            raise AppRegistryConflict()
        # Recheck requester membership and effective policy at approval time.
        if decision == "approve":
            requester = db.exec(SqlBuilder.select.table(User).where(User.id == row.requester_id)).first()
            if requester is None:
                raise AppGovernanceDenied()
            authorize_submission(db, requester, row.project_id.to_short_code() if row.project_id else None)
        claimed = db.exec(
            update(AppInstallationRequest)
            .where(
                AppInstallationRequest.id == row.id,
                AppInstallationRequest.generation == 1,
                AppInstallationRequest.decision == "pending",
            )
            .values(generation=2)
        )
        if claimed != 1:
            raise AppRegistryConflict()
        row.generation = 2
        row.decision = desired
        row.reviewer_id = actor.id
        row.reason = reason
        if decision == "approve":
            try:
                with db.savepoint():
                    if preparation_error is not None:
                        raise preparation_error
                    row.app = installer(actor, prepared, row.expected_app_revision)
                row.installation_status = "installed"
            except (ValueError, AppRegistryConflict) as exc:
                row.installation_status = "failed"
                row.installation_error = (
                    "app_revision_conflict" if isinstance(exc, AppRegistryConflict) else "package_install_failed"
                )
        db.update(row)
        return row.response()
