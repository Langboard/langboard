"""App-directed event names; dispatch requires an explicit destination binding."""

APP_EXECUTION_EVENT = "io.langboard.app.execution.requested.v1"
APP_INTERACTION_RESPONSE_EVENT = "io.langboard.app.interaction.answered.v1"
APP_CARD_ARCHIVE_EVENT = "io.langboard.app.card.archived.v1"
APP_TRANSFER_EVENT = "io.langboard.app.transfer.requested.v1"
APP_CREDENTIAL_HANDOFF_EVENT = "io.langboard.app.connection.credential.v1"
APP_NOTIFICATION_EVENT = "io.langboard.notification.requested.v1"
APP_EVENTS = {APP_NOTIFICATION_EVENT, APP_CREDENTIAL_HANDOFF_EVENT, APP_TRANSFER_EVENT, APP_EXECUTION_EVENT, APP_INTERACTION_RESPONSE_EVENT, APP_CARD_ARCHIVE_EVENT}
