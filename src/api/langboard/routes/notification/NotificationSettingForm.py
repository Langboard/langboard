from typing import Literal
from langboard_shared.core.routing import BaseFormModel, form_model
from langboard_shared.domain.models.UserNotification import NotificationType
from langboard_shared.domain.models.UserNotificationUnsubscription import NotificationChannel


@form_model
class NotificationSettingForm(BaseFormModel):
    channel: NotificationChannel
    is_unsubscribed: bool


@form_model
class NotificationSettingTypeForm(BaseFormModel):
    channel: NotificationChannel
    notification_type: NotificationType
    is_unsubscribed: bool


@form_model
class CardActivityNotificationForm(BaseFormModel):
    mode: Literal["ignore", "comments", "comments_and_moves"]
