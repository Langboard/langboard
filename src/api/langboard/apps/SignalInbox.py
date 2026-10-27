"""Bounded board Signal discovery; hidden card links never affect visible results."""

import json
import re
import time
from base64 import b64decode, b64encode
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.core.security.CollaborationChannel import CollaborationChannel
from langboard_shared.core.utils.Encryptor import Encryptor
from langboard_shared.domain.models import (
    AppConnection,
    AppResourceBinding,
    AppSignal,
    BoardAppBinding,
    Card,
    CardAppSignalBinding,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    User,
)
from langboard_shared.domain.models.ProjectRole import ProjectRoleAction
from langboard_shared.domain.services.AppGovernance import connection_discovery_scope
from langboard_shared.domain.services.AppRegistry import approved_manifests
from langboard_shared.domain.services.AppSignalProjection import (
    authorized_signal_rows,
    provider_resource_condition,
    signal_resource_name,
    signal_time_basis_expression,
    supported_signal_condition,
)
from langboard_shared.domain.services.CardVisibilityPolicy import card_visibility_scope
from langboard_shared.Env import Env
from langboard_shared.helpers import InfraHelper
from sqlalchemy import Text, and_, cast, func, or_, select
from .GitHubManifest import GitHubManifestUnavailable


_resource_name = signal_resource_name


def list_personal_signals(service, actor, after=None, *, channel=CollaborationChannel.Api, page_size=25):
    return list_board_signals(service, actor, None, after, channel=channel, provider="github", page_size=page_size)


def list_board_signals(
    service,
    actor,
    project_uid,
    after=None,
    *,
    channel=CollaborationChannel.Api,
    provider=None,
    page_size=25,
    resource_types=None,
    signal_uid=None,
):
    personal = project_uid is None
    if personal:
        from langboard_shared.domain.services.CardVisibilityPolicy import CardVisibilityContext

        if not isinstance(actor, User):
            raise GitHubManifestUnavailable()
        with DbSession.use(readonly=False) as db:
            actor = db.exec(
                SqlBuilder.select.table(User).where(User.id == actor.id).execution_options(populate_existing=True)
            ).first()
        if actor is None or actor.deleted_at or not actor.activated_at:
            raise GitHubManifestUnavailable()
        context = CardVisibilityContext(
            channel, True, True, service.card._resolve_internal_access(actor), actor_user_id=int(actor.id)
        )
        membership = (
            select(ProjectAssignedUser.id)
            .where(
                ProjectAssignedUser.project_id == Project.id,
                ProjectAssignedUser.user_id == actor.id,
            )
            .correlate(Project)
            .exists()
        )
        actions = "," + cast(ProjectRole.actions, Text) + ","
        read_grant = (
            select(ProjectRole.id)
            .where(
                ProjectRole.project_id == Project.id,
                ProjectRole.user_id == actor.id,
                or_(actions.contains(",*,"), actions.contains(",read,")),
            )
            .correlate(Project)
            .exists()
        )
        board_scope = or_(Project.owner_id == actor.id, membership & or_(actor.is_admin, read_grant))
    else:
        if service.workflow_stage._authorized_app_board(actor, project_uid, ProjectRoleAction.Read) is None:
            raise GitHubManifestUnavailable()
        resolved = service.card.resolve_visibility_context(project_uid, actor, channel)
        if resolved is None or not resolved[1].project_member:
            raise GitHubManifestUnavailable()
        project, context = resolved
        board_scope = Project.id == project.id
    if signal_uid is not None and not re.fullmatch(r"[A-Za-z0-9]{1,11}", signal_uid):
        raise ValueError("Invalid inbox signal")
    if personal and after is not None:
        try:
            if len(after) > 2048:
                raise ValueError()
            # Reject ignored suffixes, whitespace and noncanonical padding before decrypting.
            encoded = after.encode("ascii")
            if b64encode(b64decode(encoded, validate=True)) != encoded:
                raise ValueError()
            cursor = json.loads(Encryptor.decrypt(after, Env.JWT_SECRET_KEY))
            if (
                cursor["actor"] != int(actor.id)
                or cursor["scope"] != "personal-github-inbox"
                or cursor["expires"] < time.time()
            ):
                raise ValueError()
            after = cursor["after"]
        except Exception:
            raise ValueError("Invalid inbox cursor") from None
    if after is not None and not re.fullmatch(r"[A-Za-z0-9]{1,11}", after):
        raise ValueError("Invalid inbox cursor")
    manifests = approved_manifests()
    if provider is not None and (provider not in manifests or manifests[provider].signal_policy is None):
        raise ValueError("Unsupported signal provider")
    if type(page_size) is not int or not 1 <= page_size <= 25:
        raise ValueError("Invalid inbox page size")
    supported_occurrence = supported_signal_condition(manifests)
    eligibility = provider_resource_condition(manifests)
    # GitHub checks are per commit; Dokploy deployments have their own external identity.
    identity = (AppSignal.provider, AppSignal.resource_id, AppSignal.external_id, AppSignal.commit_sha)
    latest = (
        select(
            AppSignal.provider,
            AppSignal.resource_id,
            AppSignal.external_id,
            AppSignal.commit_sha,
            func.max(AppSignal.occurred_at).label("occurred_at"),
        )
        .join(AppResourceBinding, AppResourceBinding.id == AppSignal.resource_id)
        .join(BoardAppBinding, BoardAppBinding.id == AppResourceBinding.board_binding_id)
        .where(
            supported_occurrence,
            AppSignal.provider == "github" if personal else BoardAppBinding.project_id == project.id,
        )
        .group_by(*identity)
        .subquery()
    )
    time_basis = signal_time_basis_expression(manifests)
    occurrences = (
        select(
            func.max(AppSignal.id).label("signal_id"),
            func.min(AppSignal.outcome).label("minimum"),
            func.max(AppSignal.outcome).label("maximum"),
            func.min(time_basis).label("min_basis"),
            func.max(time_basis).label("max_basis"),
        )
        .join(
            latest,
            and_(
                AppSignal.provider == latest.c.provider,
                AppSignal.resource_id == latest.c.resource_id,
                AppSignal.external_id == latest.c.external_id,
                AppSignal.commit_sha == latest.c.commit_sha,
                AppSignal.occurred_at == latest.c.occurred_at,
            ),
        )
        .where(supported_occurrence)
        .group_by(*identity)
        .subquery()
    )
    linked = (
        select(CardAppSignalBinding.id)
        .join(Card, Card.id == CardAppSignalBinding.card_id)
        .where(
            Card.project_id == BoardAppBinding.project_id,
            Card.deleted_at.is_(None),
            card_visibility_scope(context),
            CardAppSignalBinding.is_enabled == True,  # noqa: E712
            CardAppSignalBinding.resource_id == AppSignal.resource_id,
            CardAppSignalBinding.external_id == AppSignal.external_id,
            CardAppSignalBinding.commit_sha == AppSignal.commit_sha,
        )
        .correlate(AppSignal, BoardAppBinding)
        .exists()
    )
    query = (
        select(
            AppSignal,
            AppResourceBinding,
            AppConnection,
            occurrences.c.minimum,
            occurrences.c.maximum,
            occurrences.c.min_basis,
            occurrences.c.max_basis,
            Project,
        )
        .join(occurrences, occurrences.c.signal_id == AppSignal.id)
        .join(AppResourceBinding, AppResourceBinding.id == AppSignal.resource_id)
        .join(BoardAppBinding, BoardAppBinding.id == AppResourceBinding.board_binding_id)
        .join(Project, Project.id == BoardAppBinding.project_id)
        .join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
        .join(User, User.id == AppConnection.owner_id)
        .where(
            board_scope,
            eligibility,
            AppSignal.provider == AppConnection.app_key,
            connection_discovery_scope(actor.id),
            ~linked,
        )
    )
    if provider is not None:
        query = query.where(AppSignal.provider == provider)
    if resource_types is not None:
        query = query.where(AppResourceBinding.resource_type.in_(resource_types))
    if signal_uid is not None:
        query = query.where(AppSignal.id == InfraHelper.convert_id(signal_uid))
    with DbSession.use(readonly=False) as db:

        def authorized(rows):
            return {row.id for row in authorized_signal_rows(db, [(row[1], row[2], None) for row in rows])}

        if after is not None:
            cursor_id = InfraHelper.convert_id(after)
            cursor_rows = db.exec(query.where(AppSignal.id == cursor_id).limit(1)).all()
            # Personal scan cursors may point to a credential-denied row. Its SQL
            # scope must still be current, but no metadata is exposed from it.
            if not cursor_rows or (not personal and cursor_rows[0][1].id not in authorized(cursor_rows)):
                raise ValueError("Invalid inbox cursor")
            query = query.where(AppSignal.id < cursor_id)
        # Bound primary reads across all boards. Credential denials consume scan
        # positions, never a result slot; continuation retains every later row.
        rows = db.exec(query.order_by(AppSignal.id.desc()).limit(101 if personal else page_size + 1)).all()
        scanned = rows[:100] if personal else rows[:page_size]
        allowed = authorized(scanned)
        items = []
        last = None
        for index, row in enumerate(scanned):
            signal, resource, connection, minimum, maximum, minimum_basis, maximum_basis, source = row
            last = signal
            if resource.id not in allowed:
                continue
            item = {
                "signal_uid": signal.get_uid(),
                "provider": signal.provider,
                "event_type": signal.event_type,
                "resource_uid": resource.get_uid(),
                "resource_type": resource.resource_type,
                "resource_name": _resource_name(resource),
                "connection_uid": connection.get_uid(),
                "external_id": signal.external_id,
                "commit_sha": signal.commit_sha,
                "occurred_at": signal.occurred_at,
                "outcome": minimum if minimum == maximum else None,
                "conflict": minimum != maximum,
                "can_bind_card": manifests[signal.provider].signal_policy is not None,
                "time_basis": minimum_basis if minimum_basis == maximum_basis else None,
                "evidence_kind": manifests[signal.provider].signal_policy.kind_for_basis(
                    minimum_basis if minimum_basis == maximum_basis else None
                ),
            }
            if personal:
                item.update(project_uid=source.get_uid(), project_title=source.title)
            items.append(item)
            if len(items) == page_size:
                break
        has_more = last is not None and any(row[0].id < last.id for row in rows)
        next_cursor = last.get_uid() if has_more else None
        if personal and next_cursor:
            next_cursor = Encryptor.encrypt(
                json.dumps(
                    {
                        "actor": int(actor.id),
                        "scope": "personal-github-inbox",
                        "expires": int(time.time()) + 3600,
                        "after": next_cursor,
                    }
                ),
                Env.JWT_SECRET_KEY,
            )
        return {"items": items, "next_cursor": next_cursor}


def create_signal_card(
    service,
    actor,
    project_uid,
    connection_uid,
    resource_uid,
    signal_uid,
    project_column_uid,
    title,
    *,
    channel=CollaborationChannel.Api,
):
    """One explicit native card creation and evidence attachment, retained across retries."""
    from langboard_shared.domain.models import CardSignalCreation, ProjectColumn
    from .CardSignal import authorized_card, authorized_signal_scope, bind_check

    title = title.strip()
    if not title or len(title) > 200:
        raise ValueError("Invalid card title")
    with DbSession.atomic() as db:
        project = service.workflow_stage._authorized_app_board(
            actor, project_uid, ProjectRoleAction.CardUpdate, lock=True
        )
        resolved = service.card.resolve_visibility_context(project_uid, actor, channel)
        if project is None or resolved is None or not resolved[1].project_member:
            raise GitHubManifestUnavailable()
        # Native provider scope locks its connection/resource before receipt lookup.
        # Concurrent requests serialize on those rows; the unique receipt is a final DB fence.
        signal, _, resource = authorized_signal_scope(
            service, db, actor, project_uid, connection_uid, resource_uid, signal_uid
        )
        column = db.exec(
            SqlBuilder.select.table(ProjectColumn)
            .where(
                ProjectColumn.id == InfraHelper.convert_id(project_column_uid),
                ProjectColumn.project_id == project.id,
                ProjectColumn.deleted_at.is_(None),
                ProjectColumn.is_archive == False,  # noqa: E712
            )
            .with_for_update()
        ).first()
        if column is None:
            raise GitHubManifestUnavailable()
        receipt = db.exec(
            SqlBuilder.select.table(CardSignalCreation)
            .where(
                CardSignalCreation.actor_id == actor.id,
                CardSignalCreation.resource_id == resource.id,
                CardSignalCreation.external_id == signal.external_id,
                CardSignalCreation.commit_sha == signal.commit_sha,
            )
            .with_for_update()
        ).first()
        if receipt:
            card = authorized_card(
                service, actor, project_uid, InfraHelper.convert_uid(receipt.card_id), channel=channel
            )
            if card.archived_at is not None:
                raise GitHubManifestUnavailable()
            return {"card_uid": card.get_uid(), "created": False}
        linked = db.exec(
            SqlBuilder.select.table(Card)
            .join(CardAppSignalBinding, CardAppSignalBinding.card_id == Card.id)
            .where(
                Card.project_id == project.id,
                Card.deleted_at.is_(None),
                Card.archived_at.is_(None),
                card_visibility_scope(resolved[1]),
                CardAppSignalBinding.resource_id == resource.id,
                CardAppSignalBinding.external_id == signal.external_id,
                CardAppSignalBinding.commit_sha == signal.commit_sha,
                CardAppSignalBinding.is_enabled == True,  # noqa: E712
            )
            .order_by(Card.id)
            .limit(1)
            .with_for_update()
        ).first()
        created = linked is None
        if created:
            result = service.card.create(actor, project, column, title, dispatch_effects=False)
            if result is None:
                raise GitHubManifestUnavailable()
            card, api_card = result
            bind_check(
                service,
                actor,
                project_uid,
                card.get_uid(),
                connection_uid,
                resource_uid,
                signal_uid,
                card.last_change_seq,
                None,
                visibility_channel=channel,
            )
            db.after_commit(lambda: service.card.dispatch_created(actor, project, column, card, {"card": api_card}))
        else:
            card = linked
        db.insert(
            CardSignalCreation(
                actor_id=actor.id,
                resource_id=resource.id,
                external_id=signal.external_id,
                commit_sha=signal.commit_sha,
                card_id=card.id,
            )
        )
        return {"card_uid": card.get_uid(), "created": created}
