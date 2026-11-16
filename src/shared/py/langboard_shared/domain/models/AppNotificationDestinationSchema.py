"""AppSchema-owned constraints for explicit personal notification consent."""

from sqlalchemy import CheckConstraint
from sqlalchemy.schema import conv
from .AppNotificationDestination import AppNotificationDestination, AppNotificationDestinationChallenge


AppNotificationDestinationChallenge.__table__.append_constraint(CheckConstraint(
    "revision >= 1 AND state IN ('pending','awaiting_user_approval','verified','cancelled')",
    name=conv("ck_app_notification_challenge_state"),
))
AppNotificationDestinationChallenge.__table__.append_constraint(CheckConstraint(
    "(state = 'pending' AND proof_id IS NULL AND proof_credential_id IS NULL AND destination_id IS NULL) OR "
    "(state = 'awaiting_user_approval' AND proof_id IS NOT NULL AND proof_credential_id IS NOT NULL AND destination_id IS NULL) OR "
    "(state = 'verified' AND proof_id IS NOT NULL AND proof_credential_id IS NOT NULL AND destination_id IS NOT NULL) OR state = 'cancelled'",
    name=conv("ck_app_notification_challenge_proof"),
))
AppNotificationDestination.__table__.append_constraint(CheckConstraint(
    "revision >= 1 AND state IN ('verified','revoked')",
    name=conv("ck_app_notification_destination_state"),
))
