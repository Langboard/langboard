"""Bounded human-only discussion reads and writes, separate from public comments."""

from sqlalchemy import and_, func, or_, select
from ...core.db import DbSession, SqlBuilder
from ...helpers import InfraHelper
from ..models import (
    Card,
    CardAttachment,
    CardComment,
    CardContentBlock,
    CardRelationship,
    Checklist,
    User,
    WhisperMessage,
    WhisperParticipant,
    WhisperThread,
)
from .CardVisibilityPolicy import CollaborationChannel


class WhisperUnavailable(Exception):
    """Identical missing/unreadable response; never disclose a hidden anchor."""


def _scope(service, actor, project_uid, card_uid, channel):
    if channel != CollaborationChannel.HumanUI or not isinstance(actor, User):
        raise WhisperUnavailable()
    contexts = service.card.resolve_work_visibility_contexts(actor, channel)
    projects = [project_id for project_id, context in contexts.items() if context.can_use_whisper]
    readable = select(Card.id).where(
        Card.project_id.in_(projects),
        Card.deleted_at.is_(None),
        Card.visibility.in_(("INTERNAL", "SHARED")),
    )
    with DbSession.use(readonly=False) as db:
        card = db.exec(
            SqlBuilder.select.table(Card).where(
                Card.id == InfraHelper.convert_id(card_uid),
                Card.project_id == InfraHelper.convert_id(project_uid),
                Card.id.in_(readable),
            )
        ).first()
    if card is None:
        raise WhisperUnavailable()
    relationships = select(CardRelationship.id).where(
        CardRelationship.card_id_parent.in_(readable),
        CardRelationship.card_id_child.in_(readable),
        or_(CardRelationship.card_id_parent == card.id, CardRelationship.card_id_child == card.id),
    )
    anchors = or_(WhisperThread.card_id == card.id, WhisperThread.relationship_id.in_(relationships))
    return card, relationships, anchors


def _thread(db, anchors, thread_uid, *, lock=False):
    query = SqlBuilder.select.table(WhisperThread).where(
        WhisperThread.id == InfraHelper.convert_id(thread_uid), anchors
    )
    if lock:
        query = query.with_for_update()
    thread = db.exec(query).first()
    if thread is None:
        raise WhisperUnavailable()
    return thread


def _project(thread):
    return {
        "uid": thread.get_uid(),
        "state": thread.state,
        "creator_uid": InfraHelper.convert_uid(thread.creator_id),
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
        "anchor": {
            "type": "CARD" if thread.card_id is not None else "RELATIONSHIP",
            "uid": InfraHelper.convert_uid(thread.card_id or thread.relationship_id),
            "subanchor_type": thread.subanchor_type,
            "subanchor_uid": thread.subanchor_uid,
        },
    }


def _message(message):
    return {
        "uid": message.get_uid(),
        "author_uid": InfraHelper.convert_uid(message.author_id),
        "content": message.content,
        "created_at": message.created_at,
        "mention_uids": message.mention_uids,
    }


def _content(content):
    if not isinstance(content, str) or not content.strip() or len(content) > 16000:
        raise ValueError("Whisper content must contain 1 to 16000 characters")
    return content


def _validate_subanchor(db, card, kind, uid):
    models = {
        "content_block": CardContentBlock,
        "comment": CardComment,
        "checklist": Checklist,
        "attachment": CardAttachment,
    }
    if kind is None and uid is None:
        return
    if kind not in models or not uid:
        raise ValueError("Invalid whisper subanchor")
    model = models[kind]
    query = select(model.id).where(model.id == InfraHelper.convert_id(uid), model.card_id == card.id)
    if hasattr(model, "deleted_at"):
        query = query.where(model.deleted_at.is_(None))
    if db.exec(query).first() is None:
        raise WhisperUnavailable()


def create_thread(
    service,
    actor,
    project_uid,
    card_uid,
    channel,
    content,
    *,
    relationship_uid=None,
    subanchor_type=None,
    subanchor_uid=None,
    mention_uids=(),
):
    card, relationships, _ = _scope(service, actor, project_uid, card_uid, channel)
    content = _content(content)
    with DbSession.atomic() as db:
        relationship_id = None
        if relationship_uid is not None:
            if subanchor_type is not None or subanchor_uid is not None:
                raise ValueError("Relationship anchors cannot have a card subanchor")
            relationship_id = InfraHelper.convert_id(relationship_uid)
            if (
                db.exec(
                    select(CardRelationship.id).where(
                        CardRelationship.id == relationship_id,
                        CardRelationship.id.in_(relationships),
                    )
                ).first()
                is None
            ):
                raise WhisperUnavailable()
        else:
            _validate_subanchor(db, card, subanchor_type, subanchor_uid)
        thread = WhisperThread(
            project_id=card.project_id,
            card_id=card.id if relationship_id is None else None,
            relationship_id=relationship_id,
            creator_id=actor.id,
            subanchor_type=subanchor_type,
            subanchor_uid=subanchor_uid,
        )
        recipients = _recipients(service, project_uid, card_uid, mention_uids, relationship_id)
        db.insert(thread)
        message = WhisperMessage(
            thread_id=thread.id,
            author_id=actor.id,
            content=content,
            mention_uids=[user.get_uid() for user in recipients],
        )
        db.insert(message)
        db.insert(WhisperParticipant(thread_id=thread.id, user_id=actor.id, last_read_message_id=message.id))
        _add_participants(db, thread, recipients)
        return {"thread": _project(thread), "message": _message(message)}


def list_threads(service, actor, project_uid, card_uid, channel, *, before_uid=None, limit=25):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    if not 1 <= limit <= 50:
        raise ValueError("Invalid page size")
    latest = select(func.max(WhisperMessage.id)).where(WhisperMessage.thread_id == WhisperThread.id).scalar_subquery()
    query = (
        select(WhisperThread, WhisperParticipant.last_read_message_id, latest)
        .outerjoin(
            WhisperParticipant,
            and_(WhisperParticipant.thread_id == WhisperThread.id, WhisperParticipant.user_id == actor.id),
        )
        .where(anchors)
        .order_by(WhisperThread.id.desc())
        .limit(limit + 1)
    )
    if before_uid:
        query = query.where(WhisperThread.id < InfraHelper.convert_id(before_uid))
    with DbSession.use(readonly=False) as db:
        rows = db.exec(query).all()
    return {
        "threads": [
            {**_project(thread), "has_unread": bool(last and (seen is None or last > seen))}
            for thread, seen, last in rows[:limit]
        ],
        "next_cursor": rows[limit - 1][0].get_uid() if len(rows) > limit else None,
    }


def read_messages(service, actor, project_uid, card_uid, channel, thread_uid, *, before_uid=None, limit=25):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    if not 1 <= limit <= 50:
        raise ValueError("Invalid page size")
    with DbSession.use(readonly=False) as db:
        thread = _thread(db, anchors, thread_uid)
        query = (
            SqlBuilder.select.table(WhisperMessage)
            .where(WhisperMessage.thread_id == thread.id)
            .order_by(WhisperMessage.id.desc())
            .limit(limit + 1)
        )
        if before_uid:
            query = query.where(WhisperMessage.id < InfraHelper.convert_id(before_uid))
        messages = db.exec(query).all()
        return {
            "thread": _project(thread),
            "messages": [_message(message) for message in reversed(messages[:limit])],
            "next_cursor": messages[limit - 1].get_uid() if len(messages) > limit else None,
        }


def reply(service, actor, project_uid, card_uid, channel, thread_uid, content, *, mention_uids=()):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    content = _content(content)
    with DbSession.atomic() as db:
        thread = _thread(db, anchors, thread_uid, lock=True)
        if thread.state != "OPEN":
            raise ValueError("Resolve state must be OPEN before replying")
        recipients = _recipients(service, project_uid, card_uid, mention_uids, thread.relationship_id)
        message = WhisperMessage(
            thread_id=thread.id,
            author_id=actor.id,
            content=content,
            mention_uids=[user.get_uid() for user in recipients],
        )
        db.insert(message)
        _add_participants(db, thread, recipients)
        _mark_read(db, thread, actor, message)
        return {"message": _message(message)}


def _mark_read(db, thread, actor, message):
    participant = db.exec(
        SqlBuilder.select.table(WhisperParticipant).where(
            WhisperParticipant.thread_id == thread.id,
            WhisperParticipant.user_id == actor.id,
        )
    ).first()
    if participant is None:
        db.insert(WhisperParticipant(thread_id=thread.id, user_id=actor.id, last_read_message_id=message.id))
    elif participant.last_read_message_id is None or message.id > participant.last_read_message_id:
        participant.last_read_message_id = message.id
        db.update(participant)


def mark_read(service, actor, project_uid, card_uid, channel, thread_uid, message_uid):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    with DbSession.use(readonly=False) as db:
        thread = _thread(db, anchors, thread_uid, lock=True)
        message = db.exec(
            SqlBuilder.select.table(WhisperMessage).where(
                WhisperMessage.id == InfraHelper.convert_id(message_uid),
                WhisperMessage.thread_id == thread.id,
            )
        ).first()
        if message is None:
            raise WhisperUnavailable()
        _mark_read(db, thread, actor, message)
        return {"read": True}


def set_state(service, actor, project_uid, card_uid, channel, thread_uid, state):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    if state not in ("OPEN", "RESOLVED"):
        raise ValueError("Invalid whisper state")
    with DbSession.use(readonly=False) as db:
        thread = _thread(db, anchors, thread_uid, lock=True)
        thread.state = state
        db.update(thread)
        return {"thread": _project(thread)}


def _recipients(service, project_uid, card_uid, user_uids, relationship_id=None):
    """Validate a bounded explicit mention list against each recipient's current access."""
    if (
        not isinstance(user_uids, (list, tuple))
        or len(user_uids) > 10
        or any(not isinstance(uid, str) or not 1 <= len(uid) <= 100 for uid in user_uids)
    ):
        raise ValueError("At most 10 recipients are allowed")
    ids = {InfraHelper.convert_id(uid) for uid in user_uids}
    if not ids:
        return []
    with DbSession.use(readonly=False) as db:
        users = db.exec(SqlBuilder.select.table(User).where(User.id.in_(ids))).all()
        if len(users) != len(ids):
            raise WhisperUnavailable()
        for user in users:
            _, relationships, _ = _scope(service, user, project_uid, card_uid, CollaborationChannel.HumanUI)
            if (
                relationship_id is not None
                and db.exec(
                    select(CardRelationship.id).where(
                        CardRelationship.id == relationship_id,
                        CardRelationship.id.in_(relationships),
                    )
                ).first()
                is None
            ):
                raise WhisperUnavailable()
    return users


def _add_participants(db, thread, users):
    if not users:
        return
    existing = {
        user_id
        for (user_id,) in db.exec(
            select(WhisperParticipant.user_id).where(
                WhisperParticipant.thread_id == thread.id,
                WhisperParticipant.user_id.in_([user.id for user in users]),
            )
        ).all()
    }
    for user in users:
        if user.id not in existing:
            db.insert(WhisperParticipant(thread_id=thread.id, user_id=user.id))


def add_participants(service, actor, project_uid, card_uid, channel, thread_uid, user_uids):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    with DbSession.atomic() as db:
        thread = _thread(db, anchors, thread_uid, lock=True)
        users = _recipients(service, project_uid, card_uid, user_uids, thread.relationship_id)
        _add_participants(db, thread, users)
    return {"added_user_uids": [user.get_uid() for user in users]}


def list_participants(service, actor, project_uid, card_uid, channel, thread_uid, *, before_uid=None, limit=25):
    _, _, anchors = _scope(service, actor, project_uid, card_uid, channel)
    if not 1 <= limit <= 50:
        raise ValueError("Invalid page size")
    with DbSession.use(readonly=False) as db:
        thread = _thread(db, anchors, thread_uid)
        query = (
            select(WhisperParticipant, User)
            .join(User, User.id == WhisperParticipant.user_id)
            .where(
                WhisperParticipant.thread_id == thread.id,
                User.deleted_at.is_(None),
                User.activated_at.is_not(None),
            )
            .order_by(WhisperParticipant.id.desc())
            .limit(limit + 1)
        )
        if before_uid:
            query = query.where(WhisperParticipant.id < InfraHelper.convert_id(before_uid))
        rows = db.exec(query).all()
    return {
        "participants": [
            {
                "user_uid": user.get_uid(),
                "firstname": user.firstname,
                "lastname": user.lastname,
                "last_read_message_uid": InfraHelper.convert_uid(participant.last_read_message_id)
                if participant.last_read_message_id
                else None,
            }
            for participant, user in rows[:limit]
        ],
        "next_cursor": rows[limit - 1][0].get_uid() if len(rows) > limit else None,
    }
