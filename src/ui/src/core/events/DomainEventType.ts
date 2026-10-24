export enum DomainEventType {
    // Bot lifecycle and scheduled delivery
    BotCreated = "bot_created",
    BotMentioned = "bot_mentioned",
    BotCronScheduled = "bot_cron_scheduled",

    // Project
    ProjectUpdated = "project_updated",
    ProjectDeleted = "project_deleted",

    // Project Label
    ProjectLabelCreated = "project_label_created",
    ProjectLabelUpdated = "project_label_updated",
    ProjectLabelDeleted = "project_label_deleted",

    // Project Wiki
    ProjectWikiCreated = "project_wiki_created",
    ProjectWikiUpdated = "project_wiki_updated",
    ProjectWikiPublicityChanged = "project_wiki_publicity_changed",
    ProjectWikiDeleted = "project_wiki_deleted",

    // Project Column
    ProjectColumnCreated = "project_column_created",
    ProjectColumnNameChanged = "project_column_name_changed",
    ProjectColumnDeleted = "project_column_deleted",

    // Card
    CardCreated = "card_created",
    CardUpdated = "card_updated",
    CardMoved = "card_moved",
    CardLabelsUpdated = "card_labels_updated",
    CardRelationshipsUpdated = "card_relationships_updated",
    CardDeleted = "card_deleted",

    // Card Attachment
    CardAttachmentUploaded = "card_attachment_uploaded",
    CardAttachmentNameChanged = "card_attachment_name_changed",
    CardAttachmentDeleted = "card_attachment_deleted",

    // Card Comment
    CardCommentAdded = "card_comment_added",
    CardCommentUpdated = "card_comment_updated",
    CardCommentDeleted = "card_comment_deleted",
    CardCommentReacted = "card_comment_reacted",
    CardCommentUnreacted = "card_comment_unreacted",

    // Card Checklist
    CardChecklistCreated = "card_checklist_created",
    CardChecklistTitleChanged = "card_checklist_title_changed",
    CardChecklistChecked = "card_checklist_checked",
    CardChecklistUnchecked = "card_checklist_unchecked",
    CardChecklistDeleted = "card_checklist_deleted",

    // Card Checkitem
    CardCheckitemCreated = "card_checkitem_created",
    CardCheckitemTitleChanged = "card_checkitem_title_changed",
    CardCheckitemTimerStarted = "card_checkitem_timer_started",
    CardCheckitemTimerPaused = "card_checkitem_timer_paused",
    CardCheckitemTimerStopped = "card_checkitem_timer_stopped",
    CardCheckitemChecked = "card_checkitem_checked",
    CardCheckitemUnchecked = "card_checkitem_unchecked",
    CardCheckitemCardified = "card_checkitem_cardified",
    CardCheckitemDeleted = "card_checkitem_deleted",
}

export const PROJECT_CATEGORIZED_EVENT_TYPES = {
    project: [DomainEventType.ProjectUpdated, DomainEventType.ProjectDeleted],
    project_label: [DomainEventType.ProjectLabelCreated, DomainEventType.ProjectLabelUpdated, DomainEventType.ProjectLabelDeleted],
};

export const WIKI_CATEGORIZED_EVENT_TYPES = {
    project_wiki: [
        DomainEventType.ProjectWikiCreated,
        DomainEventType.ProjectWikiUpdated,
        DomainEventType.ProjectWikiPublicityChanged,
        DomainEventType.ProjectWikiDeleted,
    ],
};

export const COLUMN_CATEGORIZED_EVENT_TYPES = {
    project_column: [DomainEventType.ProjectColumnNameChanged, DomainEventType.ProjectColumnDeleted],
};

export const CARD_CATEGORIZED_EVENT_TYPES = {
    card: [
        DomainEventType.CardUpdated,
        DomainEventType.CardMoved,
        DomainEventType.CardLabelsUpdated,
        DomainEventType.CardRelationshipsUpdated,
        DomainEventType.CardDeleted,
    ],
    card_attachment: [
        DomainEventType.CardAttachmentUploaded,
        DomainEventType.CardAttachmentNameChanged,
        DomainEventType.CardAttachmentDeleted,
    ],
    card_comment: [
        DomainEventType.CardCommentAdded,
        DomainEventType.CardCommentUpdated,
        DomainEventType.CardCommentDeleted,
        DomainEventType.CardCommentReacted,
        DomainEventType.CardCommentUnreacted,
    ],
    card_checklist: [
        DomainEventType.CardChecklistCreated,
        DomainEventType.CardChecklistTitleChanged,
        DomainEventType.CardChecklistChecked,
        DomainEventType.CardChecklistUnchecked,
        DomainEventType.CardChecklistDeleted,
    ],
    card_checkitem: [
        DomainEventType.CardCheckitemCreated,
        DomainEventType.CardCheckitemTitleChanged,
        DomainEventType.CardCheckitemTimerStarted,
        DomainEventType.CardCheckitemTimerPaused,
        DomainEventType.CardCheckitemTimerStopped,
        DomainEventType.CardCheckitemChecked,
        DomainEventType.CardCheckitemUnchecked,
        DomainEventType.CardCheckitemCardified,
        DomainEventType.CardCheckitemDeleted,
    ],
};
