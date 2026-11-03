"""Database checks and query indexes for native app tables.

Model fields own compound uniqueness through unique_groups. This schema owner
retains cross-column checks and named query indexes for migrations and fresh
metadata-based databases, without model __table_args__ declarations.
"""

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, text
from sqlalchemy.schema import conv
from .AppCardInteraction import AppCardInteraction
from .AppConnection import AppConnection
from .AppExecutionLease import AppExecutionLease
from .AppExecutionOutbox import AppExecutionOutbox
from .AppGovernancePolicy import AppGovernancePolicy
from .AppResourceBinding import AppResourceBinding
from .AppSignal import AppSignal
from .BoardAppBinding import BoardAppBinding
from .Card import Card
from .CardAppResourceSelection import CardAppResourceSelection
from .CardAppSignalBinding import CardAppSignalBinding
from .CardSignalCreation import CardSignalCreation
from .CardVerificationRecord import CardVerificationRecord
from .CardVisibilityChange import CardVisibilityChange
from .Checklist import Checklist
from .DocumentKeywordIndex import DocumentKeywordIndex
from .DocumentVectorCleanup import DocumentVectorCleanup
from .DokployNotificationReceipt import DokployNotificationReceipt
from .DokployWebhookBinding import DokployWebhookBinding
from .GitHubHealthJob import GitHubHealthJob
from .GitHubLifecycleReceipt import GitHubLifecycleReceipt
from .GitHubSignalDelivery import GitHubSignalDelivery
from .ProjectActivity import ProjectActivity
from .ProjectCreationReceipt import ProjectCreationReceipt
from .ProjectLabel import ProjectLabel
from .SecretReference import SecretReference
from .SecretReferenceAudit import SecretReferenceAudit
from .UserCardReadState import UserCardReadState


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


# Legacy constraint names differ from the table-derived unique_groups convention.
# Keep those identities stable for existing migrations and installed databases.
BoardAppBinding.__table__.append_constraint(
    UniqueConstraint("project_id", "app_key", name="uq_board_app_binding_project_app")
)

BoardAppBinding.__table__.append_constraint(
    CheckConstraint(
        "state IN ('disabled','enabled','needs_attention','disconnected')", name=conv("ck_board_app_binding_state")
    )
)

CardAppSignalBinding.__table__.append_constraint(
    UniqueConstraint("card_id", "resource_id", "external_id", "commit_sha", name="uq_card_app_signal_scope")
)

CardSignalCreation.__table__.append_constraint(
    UniqueConstraint("actor_id", "resource_id", "external_id", "commit_sha", name="uq_card_signal_creation_identity")
)

CardAppResourceSelection.__table__.append_constraint(
    UniqueConstraint("card_id", "app_key", name="uq_card_app_resource_selection")
)


Card.__table__.append_constraint(
    CheckConstraint("(source_type IS NULL) = (source_uid IS NULL)", name="linked_source_complete")
)

Index("ix_card_project_archive_page", Card.__table__.c.project_id, Card.__table__.c.archived_at, Card.__table__.c.id)

Card.__table__.append_constraint(
    CheckConstraint("visibility IN ('INTERNAL', 'SHARED', 'PRIVATE')", name="card_visibility")
)

Index("ix_card_project_visibility", Card.__table__.c.project_id, Card.__table__.c.visibility)

Card.__table__.append_constraint(
    CheckConstraint(
        "(visibility = 'PRIVATE' AND owner_user_id IS NOT NULL AND owner_user_id > 0 AND created_by_user_id IS NOT NULL AND owner_user_id = created_by_user_id AND created_by_bot_id IS NULL) OR (visibility <> 'PRIVATE' AND owner_user_id IS NULL)",
        name="card_private_owner",
    )
)

CardVerificationRecord.__table__.append_constraint(
    CheckConstraint("decision IN ('verified', 'partial', 'unverified')", name="verification_decision")
)

CardVerificationRecord.__table__.append_constraint(
    CheckConstraint("(recorded_by_user_id IS NULL) <> (recorded_by_bot_id IS NULL)", name="verification_actor")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint("previous_visibility IN ('INTERNAL', 'SHARED')", name="visibility_change_previous")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint("next_visibility IN ('INTERNAL', 'SHARED')", name="visibility_change_next")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint("previous_visibility <> next_visibility", name="visibility_change_distinct")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint("(changed_by_user_id IS NULL) <> (changed_by_bot_id IS NULL)", name="visibility_change_actor")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint("channel IN ('human_ui','mcp','api','bot')", name="visibility_change_channel")
)

CardVisibilityChange.__table__.append_constraint(
    CheckConstraint(
        "next_visibility <> 'SHARED' OR (channel = 'human_ui' AND changed_by_bot_id IS NULL)",
        name="visibility_change_human_share",
    )
)

Index(
    "ix_card_visibility_change_card_created",
    CardVisibilityChange.__table__.c.card_id,
    CardVisibilityChange.__table__.c.created_at,
    CardVisibilityChange.__table__.c.id,
)

Index(
    "uq_checklist_active_system_card",
    Checklist.__table__.c.card_id,
    unique=True,
    postgresql_where=text("is_system AND deleted_at IS NULL"),
    sqlite_where=text("is_system = 1 AND deleted_at IS NULL"),
)

Index("ix_document_vector_cleanup_due_at", DocumentVectorCleanup.__table__.c.due_at)
Index(
    "ix_document_keyword_index_scope",
    DocumentKeywordIndex.__table__.c.project_id,
    DocumentKeywordIndex.__table__.c.card_id,
)
Index(
    "ix_document_keyword_index_due_at",
    DocumentKeywordIndex.__table__.c.due_at,
    postgresql_where=text("due_at IS NOT NULL"),
    sqlite_where=text("due_at IS NOT NULL"),
)
DocumentKeywordIndex.__table__.append_constraint(
    CheckConstraint(
        "version >= 1 AND completed_version >= 0 AND completed_version <= version",
        name=conv("ck_document_keyword_index_versions"),
    )
)
DocumentKeywordIndex.__table__.append_constraint(
    CheckConstraint(
        "operation IN ('index', 'remove')",
        name=conv("ck_document_keyword_index_operation"),
    )
)

DokployNotificationReceipt.__table__.append_constraint(
    UniqueConstraint("config_id", "config_revision", "payload_digest", name="uq_dokploy_notification_receipt")
)

DokployWebhookBinding.__table__.append_constraint(
    UniqueConstraint("board_binding_id", "connection_id", name="uq_dokploy_webhook_binding")
)

DokployWebhookBinding.__table__.append_constraint(
    CheckConstraint("state IN ('enabled','disabled')", name=conv("ck_dokploy_webhook_state"))
)

GitHubHealthJob.__table__.append_constraint(UniqueConstraint("receipt_id", name="uq_github_health_receipt"))

GitHubHealthJob.__table__.append_constraint(
    CheckConstraint(
        "state IN ('pending','processing','completed','blocked','failed')", name=conv("ck_github_health_state")
    )
)

Index("ix_github_health_due", GitHubHealthJob.__table__.c.state, GitHubHealthJob.__table__.c.available_at)

GitHubLifecycleReceipt.__table__.append_constraint(
    UniqueConstraint("connection_id", "delivery_id", name="uq_github_lifecycle_delivery")
)

GitHubSignalDelivery.__table__.append_constraint(
    UniqueConstraint("connection_id", "event_id", name="uq_github_signal_delivery")
)

GitHubSignalDelivery.__table__.append_constraint(
    CheckConstraint(
        "state IN ('pending','processing','completed','blocked','failed')", name=conv("ck_github_signal_state")
    )
)

Index("ix_github_signal_due", GitHubSignalDelivery.__table__.c.state, GitHubSignalDelivery.__table__.c.available_at)

Index(
    "ix_project_activity_project_id_created_at",
    ProjectActivity.__table__.c.project_id,
    ProjectActivity.__table__.c.created_at,
)

Index(
    "ix_project_activity_card_id_created_at",
    ProjectActivity.__table__.c.card_id,
    ProjectActivity.__table__.c.created_at,
)

ProjectLabel.__table__.append_constraint(
    UniqueConstraint("project_id", "global_label_id", name="uq_project_label_global_source")
)

SecretReference.__table__.append_constraint(
    UniqueConstraint("scope", "scope_id", "name", name=conv("uq_secret_reference_scope_name"))
)

SecretReference.__table__.append_constraint(
    CheckConstraint("scope IN ('personal','project','workspace','instance') AND (scope != 'instance' OR scope_id = 0)", name=conv("ck_secret_reference_scope"))
)

SecretReference.__table__.append_constraint(
    CheckConstraint("state IN ('active','revoked')", name=conv("ck_secret_reference_state"))
)

SecretReferenceAudit.__table__.append_constraint(
    CheckConstraint(
        "action IN ('created','resolved','renamed','moved','revoked','rotated','bound','migrated','copied')",
        name=conv("ck_secret_reference_audit_action"),
    )
)

UserCardReadState.__table__.append_constraint(
    UniqueConstraint("user_id", "card_id", name="uq_user_card_read_state_user_card")
)

ProjectCreationReceipt.__table__.append_constraint(
    UniqueConstraint("actor_id", "request_uuid", name="uq_project_creation_receipt_actor_request")
)
