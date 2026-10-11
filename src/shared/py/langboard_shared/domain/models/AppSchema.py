"""Database checks and query indexes for native app tables.

Model fields own compound uniqueness through unique_groups. This schema owner
retains cross-column checks and named query indexes for migrations and fresh
metadata-based databases, without model __table_args__ declarations.
"""

from sqlalchemy import CheckConstraint, Index
from sqlalchemy.schema import conv
from .AppCardInteraction import AppCardInteraction
from .AppConnection import AppConnection
from .AppExecutionLease import AppExecutionLease
from .AppExecutionOutbox import AppExecutionOutbox
from .AppGovernancePolicy import AppGovernancePolicy
from .AppResourceBinding import AppResourceBinding
from .AppSignal import AppSignal


AppConnection.__table__.append_constraint(
    CheckConstraint("state IN ('pending','connected','revoked','disconnected')", name=conv("ck_app_connection_state"))
)

AppConnection.__table__.append_constraint(
    CheckConstraint(
        "(ownership = 'personal' AND organization_id IS NULL) OR "
        "(ownership = 'organization' AND organization_id IS NOT NULL)",
        name=conv("ck_app_connection_ownership"),
    )
)

AppGovernancePolicy.__table__.append_constraint(
    CheckConstraint(
        "(mode IS NOT NULL AND mode IN ('disabled','approved_only','personal_allowed')) OR (mode IS NULL AND organization_id IS NOT NULL)",
        name=conv("ck_app_governance_policy_mode"),
    )
)

AppGovernancePolicy.__table__.append_constraint(
    CheckConstraint(
        "(scope_key = 'global' AND organization_id IS NULL) OR "
        "(organization_id IS NOT NULL AND scope_key = 'organization:' || CAST(organization_id AS VARCHAR))",
        name=conv("ck_app_governance_policy_scope"),
    )
)

Index(
    "ix_app_resource_signal_lookup",
    AppResourceBinding.__table__.c.connection_id,
    AppResourceBinding.__table__.c.resource_type,
    AppResourceBinding.__table__.c.external_resource_id,
    AppResourceBinding.__table__.c.id,
)

AppResourceBinding.__table__.append_constraint(
    CheckConstraint(
        "access_state IN ('unknown','granted','denied','revoked')", name=conv("ck_app_resource_access_state")
    )
)

AppResourceBinding.__table__.append_constraint(
    CheckConstraint("health IN ('unknown','healthy','degraded','unavailable')", name=conv("ck_app_resource_health"))
)

Index(
    "ix_app_signal_check_occurrence",
    AppSignal.__table__.c.resource_id,
    AppSignal.__table__.c.event_type,
    AppSignal.__table__.c.external_id,
    AppSignal.__table__.c.commit_sha,
    AppSignal.__table__.c.occurred_at,
)

AppCardInteraction.__table__.append_constraint(
    CheckConstraint("state IN ('pending','expired','cancelled')", name="ck_app_card_interaction_state")
)

AppExecutionOutbox.__table__.append_constraint(
    CheckConstraint(
        "state IN ('pending','delivering','delivered','blocked','failed')",
        name=conv("ck_app_execution_outbox_state"),
    )
)

AppExecutionLease.__table__.append_constraint(
    CheckConstraint(
        "state IN ('authorized','stop_requested','stopped')",
        name="ck_app_execution_lease_state",
    )
)
