export enum EProjectActivityType {
    // Project
    ProjectCreated = "project_created",
    ProjectUpdated = "project_updated",
    ProjectAssignedUsersUpdated = "project_assigned_users_updated",
    ProjectInvitedUserAccepted = "project_invited_user_accepted",
    ProjectDeleted = "project_deleted",

    // Project Column
    ProjectColumnCreated = "project_column_created",
    ProjectColumnNameChanged = "project_column_name_changed",
    ProjectColumnDeleted = "project_column_deleted",

    // Project Label
    ProjectLabelCreated = "project_label_created",
    ProjectLabelUpdated = "project_label_updated",
    ProjectLabelDeleted = "project_label_deleted",

    // Card
    CardCreated = "card_created",
    CardLinked = "card_linked",
    CardCloned = "card_cloned",
    CardUpdated = "card_updated",
    CardMoved = "card_moved",
    CardAssignedUsersUpdated = "card_assigned_users_updated",
    CardLabelsUpdated = "card_labels_updated",
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

    // Card Relationships
    CardRelationshipsUpdated = "card_relationships_updated",

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

export enum EProjectWikiActivityType {
    WikiCreated = "wiki_created",
    WikiUpdated = "wiki_updated",
    WikiPublicityChanged = "wiki_publicity_changed",
    WikiAssigneesUpdated = "wiki_assignees_updated",
    WikiDeleted = "wiki_deleted",
}

export enum EUserActivityType {
    Activated = "activated",
    DeclinedProjectInvitation = "declined_project_invitation",
}
