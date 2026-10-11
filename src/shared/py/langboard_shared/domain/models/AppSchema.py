"""Database authority constraints for the app registry foundation."""

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.schema import conv
from .AppConnection import AppConnection
from .AppGovernancePolicy import AppGovernancePolicy
from .BoardAppBinding import BoardAppBinding


AppConnection.__table__.append_constraint(
    CheckConstraint("state IN ('pending','connected','revoked','disconnected')", name=conv("ck_app_connection_state"))
)
AppConnection.__table__.append_constraint(
    CheckConstraint(
        "(ownership = 'personal' AND organization_id IS NULL) OR (ownership = 'organization' AND organization_id IS NOT NULL)",
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
        "(scope_key = 'global' AND organization_id IS NULL) OR (organization_id IS NOT NULL AND scope_key = 'organization:' || CAST(organization_id AS VARCHAR))",
        name=conv("ck_app_governance_policy_scope"),
    )
)
BoardAppBinding.__table__.append_constraint(
    UniqueConstraint("project_id", "app_key", name=conv("uq_board_app_binding_project_app"))
)

BoardAppBinding.__table__.append_constraint(
    CheckConstraint(
        "state IN ('disabled','enabled','needs_attention','disconnected')", name=conv("ck_board_app_binding_state")
    )
)
