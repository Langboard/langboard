"""Occurrence-ordered provider evidence for explicit card scopes; never reviewer approval."""

import re
from sqlalchemy import Text, and_, case, cast, func, or_, select, tuple_
from ...core.db import DbSession, SqlBuilder
from ...helpers import InfraHelper
from ..models import (
    AppConnection,
    AppDefinition,
    AppGovernancePolicy,
    AppResourceBinding,
    AppSignal,
    BoardAppBinding,
    Card,
    CardAppSignalBinding,
    Organization,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    SecretReference,
    User,
)
from .AppGovernance import connection_discovery_scope


def registered_capability_condition(app_key, capability):
    """An explicit override can revoke built-in capabilities without per-row queries."""
    revoked = select(AppDefinition.id).where(
        AppDefinition.key == app_key,
        or_(
            AppDefinition.is_enabled == False,  # noqa: E712
            ~func.coalesce(cast(AppDefinition.declaration["capabilities"], Text), "").contains(f'"{capability}"'),
        ),
    ).exists()
    return ~revoked


def signal_resource_conditions(*, app_key="github", resource_type="repository", capability="signals.read"):
    """Current owner consumption authority, shared by evidence and resource discovery."""
    membership = (
        select(ProjectAssignedUser.id)
        .where(
            ProjectAssignedUser.project_id == Project.id,
            ProjectAssignedUser.user_id == User.id,
        )
        .exists()
    )
    actions = "," + cast(ProjectRole.actions, Text) + ","
    update_grant = (
        select(ProjectRole.id)
        .where(
            ProjectRole.project_id == Project.id,
            ProjectRole.user_id == User.id,
            or_(actions.contains(",*,"), actions.contains(",update,")),
        )
        .exists()
    )
    global_disabled = select(AppGovernancePolicy.id).where(
        AppGovernancePolicy.scope_key == "global", AppGovernancePolicy.mode == "disabled",
    ).exists()
    organization_disabled = select(AppGovernancePolicy.id).where(
        AppGovernancePolicy.organization_id == Project.organization_id,
        AppGovernancePolicy.mode == "disabled",
    ).correlate(Project).exists()
    return (
        registered_capability_condition(app_key, capability),
        ~global_disabled,
        ~organization_disabled,
        Project.deleted_at.is_(None),
        BoardAppBinding.app_key == app_key,
        BoardAppBinding.state.in_(["enabled", "needs_attention"]),
        cast(BoardAppBinding.granted_capabilities, Text).contains(f'"{capability}"'),
        AppConnection.app_key == BoardAppBinding.app_key,
        AppConnection.state == "connected",
        connection_discovery_scope(AppConnection.owner_id),
        User.deleted_at.is_(None),
        User.activated_at.is_not(None),
        or_(User.is_admin == True, Project.owner_id == User.id, and_(membership, update_grant)),  # noqa: E712
        AppResourceBinding.resource_type == resource_type,
        AppResourceBinding.is_selected == True,  # noqa: E712
        AppResourceBinding.access_state == "granted",
    )


def supported_signal_condition(manifests=None):
    from .AppRegistry import approved_manifests

    predicates = []
    for manifest in (approved_manifests() if manifests is None else manifests).values():
        policy = manifest.signal_policy
        if policy is None:
            continue
        conditions = [AppSignal.provider == manifest.key, AppSignal.event_type.in_(policy.event_types)]
        if policy.requires_empty_commit:
            conditions.append(AppSignal.commit_sha == "")
        if policy.requires_commit:
            conditions.append(AppSignal.commit_sha != "")
        predicates.append(and_(*conditions))
    return or_(False, *predicates)


def provider_resource_condition(manifests=None):
    from .AppRegistry import approved_manifests

    predicates = []
    for manifest in (approved_manifests() if manifests is None else manifests).values():
        policy = manifest.signal_policy
        if policy is None:
            continue
        for kind in policy.resource_types:
            predicates.append(and_(
                *signal_resource_conditions(app_key=manifest.key, resource_type=kind),
                *(registered_capability_condition(manifest.key, capability)
                  for capability in policy.required_capabilities),
                *(cast(BoardAppBinding.granted_capabilities, Text).contains(f'"{capability}"')
                  for capability in policy.required_capabilities),
            ))
    return or_(False, *predicates)


def signal_time_basis_expression(manifests):
    """Resolve approved per-event semantics for tied proof rows in SQL."""
    entries = []
    for provider, manifest in manifests.items():
        policy = manifest.signal_policy
        if policy is not None:
            entries.extend((and_(AppSignal.provider == provider, AppSignal.event_type == event_type),
                            policy.basis_for_event(event_type)) for event_type in policy.event_types)
    return case(*entries, else_=None) if entries else cast(None, Text)


def signal_resource_name(resource):
    path = resource.resource_path
    leaf = path[-1] if isinstance(path, list) and path else None
    name = leaf.get("name") if isinstance(leaf, dict) else None
    return (name if isinstance(name, str) and name.strip() else resource.external_resource_id)[:200]


def authorized_signal_rows(db, rows):
    """Rows contain a scope/resource, connection and opaque third field. No secret values."""
    actions = "," + cast(ProjectRole.actions, Text) + ","
    # Canonical references only; one bounded batch lookup, never resolve secret material.
    reference_ids = {}
    for binding, connection, _ in rows:
        uri = connection.credential_reference or ""
        if re.fullmatch(r"secret://ref/[A-Za-z0-9]{1,11}", uri):
            try:
                reference_ids[binding.id] = InfraHelper.convert_id(uri.removeprefix("secret://ref/"))
            except (ValueError, TypeError):
                continue
    from ...core.types import SafeDateTime
    from ..models import AppConnectionCredential
    from .AppConnectionAuthentication import _identity_hash, is_native_inbound_connection
    native_connections = {connection.id: connection for _, connection, _ in rows if is_native_inbound_connection(connection)}
    native_authorized = set()
    if native_connections:
        definitions = {definition.key: definition for definition in db.exec(
            SqlBuilder.select.table(AppDefinition).where(
                AppDefinition.key.in_({connection.app_key for connection in native_connections.values()}),
                AppDefinition.is_enabled == True,  # noqa: E712
            )
        ).all()}
        credentials = db.exec(SqlBuilder.select.table(AppConnectionCredential).where(
            AppConnectionCredential.connection_id.in_(native_connections),
            AppConnectionCredential.revoked_at.is_(None), AppConnectionCredential.expires_at > SafeDateTime.now(),
        )).all()
        identities = {}
        for credential in credentials:
            connection = native_connections[credential.connection_id]
            definition = definitions.get(connection.app_key)
            if definition is None:
                continue
            if connection.id not in identities:
                identities[connection.id] = _identity_hash(db, connection, definition=definition)
            if credential.identity_hash == identities[connection.id]:
                native_authorized.add(connection.id)
    native_rows = [binding for binding, connection, _ in rows if connection.id in native_authorized]
    if not reference_ids:
        return native_rows
    # Match the native SecretReference scope rules without per-card authority reads.
    scope_member = (
        select(ProjectAssignedUser.id)
        .where(
            ProjectAssignedUser.project_id == Project.id,
            ProjectAssignedUser.user_id == AppConnection.owner_id,
        )
        .correlate(Project, AppConnection)
        .exists()
    )
    scope_update = (
        select(ProjectRole.id)
        .where(
            ProjectRole.project_id == Project.id,
            ProjectRole.user_id == AppConnection.owner_id,
            or_(actions.contains(",*,"), actions.contains(",update,")),
        )
        .correlate(Project, AppConnection)
        .exists()
    )
    project_scope = (
        select(Project.id)
        .where(
            Project.id == SecretReference.scope_id,
            Project.deleted_at.is_(None),
            or_(
                User.is_admin == True,  # noqa: E712
                Project.owner_id == AppConnection.owner_id,  # noqa: E712
                and_(scope_member, scope_update),
            ),
        )
        .correlate(SecretReference, AppConnection, User)
        .exists()
    )
    workspace_scope = (
        select(Organization.id)
        .where(
            Organization.id == SecretReference.scope_id,
            Organization.is_active == True,  # noqa: E712
            Organization.suspended_at.is_(None),
            Organization.owner_user_id == AppConnection.owner_id,
        )
        .correlate(SecretReference, AppConnection)
        .exists()
    )
    pairs = {
        (reference_ids[binding.id], connection.id) for binding, connection, _ in rows if binding.id in reference_ids
    }
    authorized = set(
        db.exec(
            select(SecretReference.id, AppConnection.id)
            .join(
                AppConnection,
                tuple_(SecretReference.id, AppConnection.id).in_(pairs),
            )
            .join(User, User.id == AppConnection.owner_id)
            .where(
                SecretReference.state == "active",
                or_(
                    and_(SecretReference.scope == "personal", SecretReference.scope_id == AppConnection.owner_id),
                    and_(SecretReference.scope == "project", project_scope),
                    and_(SecretReference.scope == "workspace", workspace_scope),
                ),
            )
        ).all()
    )
    eligible = [
        binding for binding, connection, _ in rows if (reference_ids.get(binding.id), connection.id) in authorized
    ]
    return eligible + native_rows


def card_signal_projections(cards):
    """Caller supplies its authorized card batch; current consumer authority is checked on primary."""
    from .AppRegistry import approved_manifests

    if not cards:
        return {}
    card_ids = [card.id for card in cards]
    with DbSession.use(readonly=False) as db:
        # Unlinked batches need neither global registry parsing nor authority joins.
        # A positive probe still requires every current authority check below.
        linked = db.exec(
            select(CardAppSignalBinding.id).where(
                CardAppSignalBinding.card_id.in_(card_ids),
                CardAppSignalBinding.is_enabled == True,  # noqa: E712
            ).limit(1)
        ).first()
        if linked is None:
            return {}
        manifests = approved_manifests()
        rows = db.exec(
            select(CardAppSignalBinding, AppConnection, Card.last_change_seq, AppResourceBinding)
            .join(
                AppResourceBinding,
                AppResourceBinding.id == CardAppSignalBinding.resource_id,
            )
            .join(BoardAppBinding, BoardAppBinding.id == AppResourceBinding.board_binding_id)
            .join(
                Card,
                Card.id == CardAppSignalBinding.card_id,
            )
            .join(Project, Project.id == Card.project_id)
            .join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
            .join(
                User,
                User.id == AppConnection.owner_id,
            )
            .where(
                Card.id.in_(card_ids),
                Card.deleted_at.is_(None),
                provider_resource_condition(manifests),
                BoardAppBinding.project_id == Card.project_id,
                CardAppSignalBinding.is_enabled == True,  # noqa: E712
            )
        ).all()
        if not rows:
            return {}
        eligible = authorized_signal_rows(
            db, [(binding, connection, revision) for binding, connection, revision, _ in rows]
        )
        if not eligible:
            return {}
        matching = and_(
            AppSignal.resource_id == CardAppSignalBinding.resource_id,
            supported_signal_condition(manifests),
            AppSignal.provider == AppConnection.app_key,
            AppSignal.external_id == CardAppSignalBinding.external_id,
            AppSignal.commit_sha == CardAppSignalBinding.commit_sha,
        )
        latest = (
            select(CardAppSignalBinding.id.label("binding_id"), func.max(AppSignal.occurred_at).label("occurred_at"))
            .join(AppResourceBinding, AppResourceBinding.id == CardAppSignalBinding.resource_id)
            .join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
            .join(
                AppSignal,
                matching,
            )
            .where(CardAppSignalBinding.id.in_([binding.id for binding in eligible]))
            .group_by(CardAppSignalBinding.id)
            .subquery()
        )
        time_basis = signal_time_basis_expression(manifests)
        summary = (
            select(
                CardAppSignalBinding.id.label("binding_id"),
                func.min(AppSignal.outcome).label("min_outcome"),
                func.max(AppSignal.outcome).label("max_outcome"),
                func.max(AppSignal.id).label("signal_id"),
                latest.c.occurred_at,
                func.min(time_basis).label("min_basis"),
                func.max(time_basis).label("max_basis"),
            )
            .join(AppResourceBinding, AppResourceBinding.id == CardAppSignalBinding.resource_id)
            .join(AppConnection, AppConnection.id == AppResourceBinding.connection_id)
            .join(latest, latest.c.binding_id == CardAppSignalBinding.id)
            .join(
                AppSignal,
                and_(matching, AppSignal.occurred_at == latest.c.occurred_at),
            )
            .group_by(CardAppSignalBinding.id, latest.c.occurred_at)
            .subquery()
        )
        evidence = db.exec(
            select(
                summary.c.binding_id, summary.c.min_outcome, summary.c.max_outcome,
                summary.c.signal_id, summary.c.occurred_at, AppSignal.event_type,
                summary.c.min_basis, summary.c.max_basis,
            ).join(AppSignal, AppSignal.id == summary.c.signal_id)
        ).all()
    by_binding = {row[0]: row for row in evidence}
    current_revisions = {binding.card_id: revision for binding, _, revision, _ in rows}
    resources = {binding.id: (connection.app_key, resource) for binding, connection, _, resource in rows}
    outcome_states = {
        provider: dict(manifests[provider].signal_policy.outcome_states)
        for provider, _ in resources.values()
    }
    result = {}
    for binding in eligible:
        provider, resource = resources[binding.id]
        policy = manifests[provider].signal_policy
        proof = by_binding.get(binding.id)
        state = "stale" if binding.source_change_seq != current_revisions[binding.card_id] else "unavailable"
        outcome = None
        if state != "stale" and proof:
            outcome = proof[1] if proof[1] == proof[2] else None
            state = "conflict" if outcome is None else outcome_states[provider].get(outcome, "unknown")
        basis = policy.basis_for_event(proof[5] if proof else None)
        if proof and proof[6] != proof[7]:
            basis = None
        result.setdefault(binding.card_id, []).append(
            {
                "binding_uid": binding.get_uid(),
                "provider": provider,
                "event_type": proof[5] if proof and (proof[1] == proof[2] or policy.event_time_bases) else None,
                "resource_type": resource.resource_type,
                "resource_name": signal_resource_name(resource),
                "resource_uid": InfraHelper.convert_uid(binding.resource_id),
                "external_id": binding.external_id,
                "commit_sha": binding.commit_sha,
                "source_change_seq": binding.source_change_seq,
                "revision": binding.revision,
                "state": state,
                "outcome": outcome,
                "signal_uid": InfraHelper.convert_uid(proof[3]) if proof else None,
                "occurred_at": proof[4] if proof else None,
                "time_basis": basis,
                "evidence_kind": policy.kind_for_basis(basis),
            }
        )
    return result
