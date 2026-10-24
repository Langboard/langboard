"""Current-authority board-local Teams. No role, assignment or invitation writes."""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import delete, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from ...core.db import DbSession, SqlBuilder
from ...core.db.DbEngine import DbEngine
from ...domain.models import (
    BoardTeam,
    BoardTeamCommand,
    BoardTeamMember,
    Card,
    DecisionCase,
    Organization,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ScimGroup,
    ScimGroupMember,
    User,
)
from ...domain.models.ProjectRole import ProjectRoleAction
from ...domain.models.SettingRole import SettingRoleAction
from .factory.GroupDirectoryService import GroupDirectoryDenied, GroupDirectoryService


class TeamDenied(Exception):
    pass


class TeamMissing(Exception):
    pass


class TeamConflict(Exception):
    pass


DECISION_IMPACT = {"status": "unavailable", "reason": "reload_required"}


class BoardTeams:
    @contextmanager
    def _transaction(self, *, writing=False):
        if DbSession.has_active_transaction():
            raise TeamConflict("Team commands require an independent transaction")
        dialect = DbEngine.get_main_engine().dialect.name
        try:
            with DbSession.atomic(sensitive=True) as db:
                if dialect == "sqlite":
                    db.exec(text("BEGIN IMMEDIATE" if writing else "BEGIN"))
                elif dialect == "postgresql":
                    if writing:
                        db.exec(text("SET LOCAL lock_timeout = '3s'"))
                        db.exec(text("SET LOCAL statement_timeout = '10s'"))
                        # ponytail: native writers share no row/tenant mutex for absent
                        # membership/ACL rows. Narrow only after every writer cooperates.
                        db.exec(
                            text(
                                'LOCK TABLE organization, "user", setting_role, scim_group, scim_group_member, project, project_role, project_assigned_user, board_team, board_team_member, board_team_command IN SHARE ROW EXCLUSIVE MODE'
                            )
                        )
                    else:
                        db.exec(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                else:
                    raise RuntimeError("Teams require SQLite or PostgreSQL")
                yield db
        except IntegrityError as exc:
            raise TeamConflict("Team name/member/request conflict") from exc
        except OperationalError as exc:
            if getattr(exc.orig, "sqlstate", None) in ("55P03", "57014", "40P01", "40001") or getattr(
                exc.orig, "sqlite_errorcode", None
            ) in (5, 6):
                raise TeamConflict("Retry after current state is available") from exc
            raise

    def _authorized(self, db, actor_id, project_uid, *, manage=False, group=False):
        actor = db.exec(SqlBuilder.select.table(User).where(User.id == actor_id)).first()
        if actor is None or actor.deleted_at or not actor.activated_at:
            raise TeamDenied()
        project = db.exec(
            SqlBuilder.select.table(Project).where(Project.id == GroupDirectoryService._uid(project_uid))
        ).first()
        if project is None:
            raise TeamMissing()
        organizations = db.exec(SqlBuilder.select.table(Organization)).all()
        if (
            len(organizations) != 1
            or not organizations[0].is_active
            or organizations[0].suspended_at
            or project.organization_id != organizations[0].id
        ):
            raise TeamDenied()
        roles = db.exec(
            SqlBuilder.select.table(ProjectRole).where(
                ProjectRole.project_id == project.id, ProjectRole.user_id == actor.id
            )
        ).all()
        assigned = db.exec(
            SqlBuilder.select.table(ProjectAssignedUser).where(
                ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id == actor.id
            )
        ).first()
        owner_admin = actor.is_admin or project.owner_id == actor.id
        actions = [ProjectRoleAction.Read, ProjectRoleAction.Update] if manage else [ProjectRoleAction.Read]
        if not owner_admin and not (assigned and any(role.is_granted(actions) for role in roles)):
            raise TeamDenied()
        if group:
            try:
                GroupDirectoryService._authorized(db, actor.id, SettingRoleAction.GroupRead)
            except GroupDirectoryDenied as exc:
                raise TeamDenied() from exc
        return actor, project

    @staticmethod
    def _team(db, project, team_uid):
        team = db.exec(
            SqlBuilder.select.table(BoardTeam).where(BoardTeam.id == GroupDirectoryService._uid(team_uid))
        ).first()
        if team is None:
            raise TeamMissing()
        if team.project_id != project.id:
            raise TeamDenied()
        return team

    @staticmethod
    def _participants(db, project):
        users = db.exec(
            SqlBuilder.select.table(User)
            .where(
                User.deleted_at.is_(None),
                User.activated_at.is_not(None),
                or_(
                    User.id == project.owner_id,
                    User.id.in_(
                        SqlBuilder.select.column(ProjectAssignedUser.user_id).where(
                            ProjectAssignedUser.project_id == project.id
                        )
                    ),
                ),
            )
            .order_by(User.id)
        ).all()
        return {user.id: user for user in users}

    @staticmethod
    def _user(user):
        return {
            "uid": user.get_uid(),
            "firstname": user.firstname,
            "lastname": user.lastname,
            "username": user.username,
        }

    def _payload(self, db, team, participants):
        ids = db.exec(
            SqlBuilder.select.column(BoardTeamMember.user_id)
            .where(BoardTeamMember.team_id == team.id)
            .order_by(BoardTeamMember.user_id)
        ).all()
        users = [participants[user_id] for user_id in ids if user_id in participants]
        return {
            "uid": team.get_uid(),
            "project_uid": team.project_id.to_short_code(),
            "name": team.name,
            "revision": team.revision,
            "member_uids": [user.get_uid() for user in users],
            "members": [self._user(user) for user in users],
            "member_count": len(users),
        }

    def list(self, actor_id, project_uid, *, query="", page=1, limit=15):
        GroupDirectoryService._validate(page, limit, query)
        with self._transaction() as db:
            actor, project = self._authorized(db, actor_id, project_uid)
            filters = [BoardTeam.project_id == project.id, *GroupDirectoryService._search((BoardTeam.name,), query)]
            count = db.exec(SqlBuilder.select.count(BoardTeam, BoardTeam.id).where(*filters)).first()
            teams = db.exec(
                SqlBuilder.select.table(BoardTeam)
                .where(*filters)
                .order_by(BoardTeam.name, BoardTeam.id)
                .offset((page - 1) * limit)
                .limit(limit)
            ).all()
            participants = self._participants(db, project)
            try:
                self._authorized(db, actor.id, project_uid, manage=True)
                manage = True
            except TeamDenied:
                manage = False
            try:
                self._authorized(db, actor.id, project_uid, manage=True, group=True)
                group = True
            except TeamDenied:
                group = False
            return {
                "records": [self._payload(db, team, participants) for team in teams],
                "total_records": count,
                "page": page,
                "limit": limit,
                "abilities": {"manage": manage, "import_group": group},
            }

    def _decision_impact(self, db, actor, project, team):
        # Local imports avoid the actor resolver's existing BoardTeams dependency.
        from ...core.security.CollaborationChannel import CollaborationChannel
        from .CardVisibilityPolicy import card_visibility_scope
        from .DecisionFlow import DecisionFlow, card_target_revision
        from .DomainService import DomainService

        service = DomainService()
        channel = CollaborationChannel.Api
        resolved = service.card.resolve_visibility_context(project.id, actor, channel)
        if resolved is None or not resolved[1].project_member:
            return {"status": "unavailable", "reason": "current_access_unavailable"}
        # Filter visibility in SQL before inspecting provenance. Team management
        # grants no access to private cards, counts, or Decision details.
        cases = db.exec(
            select(DecisionCase, Card)
            .join(Card, (Card.id == DecisionCase.card_id) & (Card.project_id == DecisionCase.project_id))
            .where(
                DecisionCase.project_id == project.id,
                DecisionCase.state == "pending",
                Card.deleted_at.is_(None),
                card_visibility_scope(resolved[1]),
            )
        ).all()
        flow = DecisionFlow(service)
        now = datetime.now(timezone.utc)
        for case, card in cases:
            snapshot = case.actor_snapshot
            if (
                not snapshot
                or snapshot.get("project_uid") != project.get_uid()
                or not any(
                    source.get("kind") == "team" and source.get("uid") == team.get_uid()
                    for source in snapshot.get("sources", [])
                )
                or card.is_linked_resource
                or case.target_revision != card_target_revision(card)
            ):
                continue
            if case.source_kind == "poll":
                state = flow._poll_data(db, case, card, channel, now)[3].state
            else:
                state = flow._evaluation(db, case, card, channel, now).state
            if state == "pending":
                return {"status": "available", "ongoing": True, "scope": "readable_cards"}
        return {"status": "available", "ongoing": False, "scope": "readable_cards"}

    def get(self, actor_id, project_uid, team_uid):
        with self._transaction() as db:
            actor, project = self._authorized(db, actor_id, project_uid)
            team = self._team(db, project, team_uid)
            return {
                "team": self._payload(db, team, self._participants(db, project)),
                "decision_impact": self._decision_impact(db, actor, project, team),
            }

    def candidates(self, actor_id, project_uid, *, query="", page=1, limit=15):
        GroupDirectoryService._validate(page, limit, query)
        with self._transaction() as db:
            _, project = self._authorized(db, actor_id, project_uid, manage=True)
            participants = self._participants(db, project)
            filters = [
                User.id.in_(participants),
                *GroupDirectoryService._search((User.firstname, User.lastname, User.username, User.email), query),
            ]
            count = db.exec(SqlBuilder.select.count(User, User.id).where(*filters)).first()
            users = db.exec(
                SqlBuilder.select.table(User).where(*filters).order_by(User.id).offset((page - 1) * limit).limit(limit)
            ).all()
            return {
                "records": [self._user(user) for user in users],
                "total_records": count,
                "page": page,
                "limit": limit,
            }

    def _group_preview(self, db, project, team, group_uid):
        group = db.exec(
            SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == GroupDirectoryService._uid(group_uid))
        ).first()
        if group is None:
            raise TeamMissing()
        group_ids = set(
            db.exec(SqlBuilder.select.column(ScimGroupMember.user_id).where(ScimGroupMember.group_id == group.id)).all()
        )
        participants = self._participants(db, project)
        eligible_ids = sorted(group_ids & participants.keys())
        existing = set(
            db.exec(SqlBuilder.select.column(BoardTeamMember.user_id).where(BoardTeamMember.team_id == team.id)).all()
        )
        targets = [user_id for user_id in eligible_ids if user_id not in existing]
        facts = [
            group.model_dump(mode="json"),
            team.model_dump(mode="json"),
            sorted(str(i) for i in group_ids),
            sorted(str(i) for i in existing),
            project.model_dump(mode="json"),
            [(str(uid), u.email, str(u.activated_at), str(u.deleted_at)) for uid, u in participants.items()],
        ]
        digest = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
        return {
            "team_uid": team.get_uid(),
            "team_revision": team.revision,
            "group": {"uid": group_uid, "display_name": group.display_name, "revision": group.revision},
            "eligible_count": len(eligible_ids),
            "existing_count": len(set(eligible_ids) & existing),
            "target_count": len(targets),
            "excluded_count": len(group_ids) - len(eligible_ids),
            "records": [self._user(participants[uid]) for uid in eligible_ids],
            "target_uids": [uid.to_short_code() for uid in targets],
            "snapshot": digest,
        }

    def group_preview(self, actor_id, project_uid, team_uid, group_uid):
        with self._transaction() as db:
            _, project = self._authorized(db, actor_id, project_uid, manage=True, group=True)
            return self._group_preview(db, project, self._team(db, project, team_uid), group_uid)

    def mutate(
        self,
        actor_id,
        project_uid,
        action,
        *,
        request_id,
        team_uid=None,
        expected_revision=None,
        name=None,
        user_uids=None,
        group_uid=None,
        snapshot=None,
        confirmed_count=None,
        confirmed=None,
    ):
        if action not in ("create", "rename", "delete", "add_members", "remove_members", "group_add"):
            raise ValueError("Invalid Team command")
        request_id = str(UUID(str(request_id)))
        if action in ("create", "rename"):
            name = GroupDirectoryService._name(name)
        if action != "create" and (type(expected_revision) is not int or expected_revision < 1):
            raise ValueError("Team revision required")
        member_ids = []
        if action in ("add_members", "remove_members"):
            if not isinstance(user_uids, list) or not 1 <= len(user_uids) <= 100:
                raise ValueError("Explicit member delta required")
            member_ids = sorted({GroupDirectoryService._uid(uid) for uid in user_uids})
        if action == "group_add" and (
            confirmed is not True
            or type(confirmed_count) is not int
            or not isinstance(snapshot, str)
            or len(snapshot) != 64
        ):
            raise ValueError("Explicit Group snapshot confirmation required")
        payload = [
            project_uid,
            team_uid,
            action,
            expected_revision,
            name,
            [str(uid) for uid in member_ids],
            group_uid,
            snapshot,
            confirmed_count,
        ]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self._transaction(writing=True) as db:
            actor, project = self._authorized(db, actor_id, project_uid, manage=True, group=action == "group_add")
            # Recheck current board/Group authority before any durable replay.
            receipt = db.exec(
                SqlBuilder.select.table(BoardTeamCommand).where(
                    BoardTeamCommand.actor_id == actor.id, BoardTeamCommand.request_id == request_id
                )
            ).first()
            if receipt:
                if receipt.fingerprint != fingerprint:
                    raise TeamConflict("Request UUID belongs to another payload")
                return {**receipt.result, "replayed": True}
            if action == "create":
                team = BoardTeam(project_id=project.id, name=name)
                db.insert(team)
                changed = 1
            else:
                team = self._team(db, project, team_uid)
                if team.revision != expected_revision:
                    raise TeamConflict("Team changed; reload before editing")
                changed = 0
                if action == "rename":
                    changed = int(team.name != name)
                    team.name = name
                elif action == "delete":
                    db.exec(delete(BoardTeamMember).where(BoardTeamMember.team_id == team.id))
                    changed = 1
                else:
                    if action == "group_add":
                        preview = self._group_preview(db, project, team, group_uid)
                        if preview["snapshot"] != snapshot or preview["target_count"] != confirmed_count:
                            raise TeamConflict("Group or board changed; preview again")
                        member_ids = [GroupDirectoryService._uid(uid) for uid in preview["target_uids"]]
                    participants = self._participants(db, project)
                    if action != "remove_members" and not set(member_ids) <= participants.keys():
                        raise ValueError("Team members must be active board participants")
                    existing = db.exec(
                        SqlBuilder.select.table(BoardTeamMember).where(
                            BoardTeamMember.team_id == team.id, BoardTeamMember.user_id.in_(member_ids)
                        )
                    ).all()
                    existing_ids = {m.user_id for m in existing}
                    if action == "remove_members":
                        for member in existing:
                            db.delete(member)
                        changed = len(existing)
                    else:
                        for user_id in set(member_ids) - existing_ids:
                            db.insert(BoardTeamMember(team_id=team.id, user_id=user_id))
                        changed = len(set(member_ids) - existing_ids)
                if changed:
                    team.revision += 1
                if action == "delete":
                    db.delete(team)
                elif changed:
                    db.update(team)
            result = {
                "team_uid": team.get_uid(),
                "revision": team.revision,
                "changed_count": changed,
                "deleted": action == "delete",
                "replayed": False,
                "decision_impact": DECISION_IMPACT.copy(),
            }
            db.insert(
                BoardTeamCommand(
                    actor_id=actor.id,
                    project_id=project.id,
                    request_id=request_id,
                    fingerprint=fingerprint,
                    result=result,
                )
            )
            return result
