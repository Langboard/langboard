"""Current proposal and poll actor selection facts. Preview is not an authorization capability.

Create callers resolve again inside their serialized write transaction;
this module neither persists a round nor grants response or execution authority.
"""

from dataclasses import asdict
from sqlalchemy import or_
from ...core.db import DbSession, SqlBuilder
from ...core.security.CollaborationChannel import CollaborationChannel
from ...Env import Env
from ..models import (
    BoardTeam,
    BoardTeamMember,
    EmployeeMembershipPolicy,
    Organization,
    Project,
    ProjectAssignedUser,
    ProjectRole,
    ScimGroup,
    ScimGroupMember,
    SettingRole,
    User,
    UserIdentityLink,
)
from ..models.SettingRole import SettingRoleAction
from .BoardTeams import BoardTeams, TeamDenied, TeamMissing
from .DecisionFlow import DecisionDenied, DecisionFlow, DecisionMissing, _hash, _uid, card_target_revision
from .factory.GroupDirectoryService import GroupDirectoryDenied, GroupDirectoryService


MAX_ACTORS = 256
MAX_SOURCES = 64
SUPPORTED_SOURCES = ("proposal", "poll")


def _facts(rows):
    return [row.model_dump(mode="json") for row in rows]


def _principal(user):
    return {
        "uid": user.get_uid(),
        "activated_at": str(user.activated_at),
        "deleted_at": str(user.deleted_at),
        "is_admin": user.is_admin,
    }


class DecisionActors:
    def __init__(self, service):
        self.service = service
        self.flow = DecisionFlow(service)

    @staticmethod
    def _source_kind(source_kind):
        if source_kind not in SUPPORTED_SOURCES:
            raise ValueError("Only nonexecuting proposal or poll selection is supported")

    def _authorized(self, db, actor, project_uid, card_uid, channel):
        if not isinstance(actor, User):
            raise DecisionDenied()
        try:
            current, _ = BoardTeams()._authorized(db, actor.id, project_uid)
        except TeamDenied as exc:
            raise DecisionDenied() from exc
        except TeamMissing as exc:
            raise DecisionMissing() from exc
        project, card = self.flow._authorized(db, current, project_uid, card_uid, channel, manage=True)
        try:
            GroupDirectoryService._authorized(db, current.id, SettingRoleAction.GroupRead)
            group = True
        except GroupDirectoryDenied:
            group = False
        return current, project, card, {"select_user": True, "select_team": True, "select_group": group}

    @staticmethod
    def _selectors(sources):
        if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
            raise ValueError("Select between 1 and 64 sources")
        unique = {}
        for source in sources:
            if (
                type(source) is not dict
                or set(source) != {"kind", "uid"}
                or source["kind"] not in ("user", "group", "team")
            ):
                raise ValueError("Explicit user, group or team source required")
            _uid(source["uid"])
            unique[(source["kind"], source["uid"])] = dict(source)
        return [unique[key] for key in sorted(unique)]

    def preview(self, actor, project_uid, card_uid, sources, *, source_kind, channel=CollaborationChannel.Api):
        self._source_kind(source_kind)
        with self.flow._transaction(read_only=True) as db:
            return self.resolve_in_transaction(
                db, actor, project_uid, card_uid, sources, source_kind=source_kind, channel=channel
            )

    @staticmethod
    def retained_actor_uids(db, snapshot):
        """Intersect frozen provenance with live sources; never expand new members.

        A captured explicit user selector survives only the separate native current
        authority check. Deleted sources supply no membership; overlapping captured
        sources may keep an existing actor eligible. No source facts are disclosed.
        """
        organizations = db.exec(SqlBuilder.select.table(Organization)).all()
        project = db.exec(SqlBuilder.select.table(Project).where(Project.id == _uid(snapshot["project_uid"]))).first()
        if (
            len(organizations) != 1
            or not organizations[0].is_active
            or organizations[0].suspended_at
            or project is None
            or project.organization_id != organizations[0].id
        ):
            return frozenset()
        selected = snapshot["sources"]
        frozen_ids = {_uid(uid) for uid in snapshot["actor_uids"]}
        group_ids = {_uid(source["uid"]) for source in selected if source["kind"] == "group"}
        team_ids = {_uid(source["uid"]) for source in selected if source["kind"] == "team"}
        groups = (
            db.exec(
                SqlBuilder.select.table(ScimGroupMember)
                .join(ScimGroup, ScimGroup.id == ScimGroupMember.group_id)
                .where(ScimGroupMember.group_id.in_(group_ids), ScimGroupMember.user_id.in_(frozen_ids))
            ).all()
            if group_ids
            else []
        )
        teams = (
            db.exec(
                SqlBuilder.select.table(BoardTeamMember)
                .join(BoardTeam, BoardTeam.id == BoardTeamMember.team_id)
                .where(
                    BoardTeamMember.team_id.in_(team_ids),
                    BoardTeamMember.user_id.in_(frozen_ids),
                    BoardTeam.project_id == _uid(snapshot["project_uid"]),
                )
            ).all()
            if team_ids
            else []
        )
        membership = {("group", row.group_id, row.user_id) for row in groups}
        membership.update(("team", row.team_id, row.user_id) for row in teams)
        return frozenset(
            actor["uid"]
            for actor in snapshot["actors"]
            if any(
                source["kind"] == "user"
                and source["uid"] == actor["uid"]
                or (source["kind"], _uid(source["uid"]), _uid(actor["uid"])) in membership
                for source in actor["sources"]
            )
        )

    def authorize_sources_in_transaction(
        self, db, actor, project_uid, card_uid, sources, *, source_kind, channel=CollaborationChannel.Api
    ):
        """Check current source visibility without expanding membership for replay.

        Group labels, membership and frozen provenance require current GroupRead;
        Team sources must remain in the current authorized board. This check never
        treats a previous preview or immutable case as an authority capability.
        """
        if not isinstance(db, DbSession) or DbSession._atomic_session.get() is not db:
            raise RuntimeError("Source authorization requires an active atomic transaction")
        self._source_kind(source_kind)
        sources = self._selectors(sources)
        actor, project, card, abilities = self._authorized(db, actor, project_uid, card_uid, channel)
        for source in sources:
            if source["kind"] == "group":
                if not abilities["select_group"]:
                    raise DecisionDenied()
                row = db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == _uid(source["uid"]))).first()
                if row is None:
                    raise DecisionMissing()
            elif source["kind"] == "team":
                row = db.exec(SqlBuilder.select.table(BoardTeam).where(BoardTeam.id == _uid(source["uid"]))).first()
                if row is None:
                    raise DecisionMissing()
                if row.project_id != project.id:
                    raise DecisionDenied()
        return sources, actor, project, card, abilities

    def resolve_in_transaction(
        self, db, actor, project_uid, card_uid, sources, *, source_kind, channel=CollaborationChannel.Api
    ):
        """Use only the caller's active atomic DB unit; caller owns write serialization."""
        if not isinstance(db, DbSession) or DbSession._atomic_session.get() is not db:
            raise RuntimeError("Resolution requires the caller's active atomic transaction")
        self._source_kind(source_kind)
        sources = self._selectors(sources)
        actor, project, card, abilities = self._authorized(db, actor, project_uid, card_uid, channel)
        expanded, raw, ids = [], [], set()
        for source in sources:
            source_id = _uid(source["uid"])
            kind = source["kind"]
            if kind == "group":
                if not abilities["select_group"]:
                    raise DecisionDenied()
                row = db.exec(SqlBuilder.select.table(ScimGroup).where(ScimGroup.id == source_id)).first()
                if row is None:
                    raise DecisionMissing()
                members = db.exec(
                    SqlBuilder.select.column(ScimGroupMember.user_id)
                    .where(ScimGroupMember.group_id == row.id)
                    .order_by(ScimGroupMember.user_id)
                    .limit(MAX_ACTORS + 1)
                ).all()
                label = row.display_name
            elif kind == "team":
                row = db.exec(SqlBuilder.select.table(BoardTeam).where(BoardTeam.id == source_id)).first()
                if row is None:
                    raise DecisionMissing()
                if row.project_id != project.id:
                    raise DecisionDenied()
                members = db.exec(
                    SqlBuilder.select.column(BoardTeamMember.user_id)
                    .where(BoardTeamMember.team_id == row.id)
                    .order_by(BoardTeamMember.user_id)
                    .limit(MAX_ACTORS + 1)
                ).all()
                label = row.name
            else:
                row, members, label = None, [source_id], "Individual"
            ids.update(members)
            if len(ids) > MAX_ACTORS:
                raise ValueError("At most 256 expanded actors may be selected")
            membership = {
                "source": source,
                "row": row.model_dump(mode="json") if row else None,
                "members": sorted(str(member) for member in members),
            }
            raw.append(membership)
            expanded.append((source, label, set(members), _hash(membership)))
        users = (
            db.exec(SqlBuilder.select.table(User, with_deleted=True).where(User.id.in_(ids)).order_by(User.id)).all()
            if ids
            else []
        )
        assignments = (
            db.exec(
                SqlBuilder.select.table(ProjectAssignedUser)
                .where(ProjectAssignedUser.project_id == project.id, ProjectAssignedUser.user_id.in_(ids))
                .order_by(ProjectAssignedUser.id)
            ).all()
            if ids
            else []
        )
        board_ids = {assignment.user_id for assignment in assignments} | {project.owner_id}
        eligible = self.flow._authorization(db, [user.get_uid() for user in users], project.id, card, channel)
        eligible_users = {
            user.id: user
            for user in users
            if user.id in board_ids and user.get_uid() in eligible and user.activated_at and not user.deleted_at
        }
        summaries = []
        for source, label, members, revision in expanded:
            included = members & eligible_users.keys()
            summaries.append(
                {
                    **source,
                    "label": label,
                    "revision": revision,
                    "membership_count": len(members),
                    "eligible_count": len(included),
                    "excluded_count": len(members) - len(included),
                    "exclusions": {"not_currently_eligible": len(members) - len(included)},
                }
            )
        actors = [
            {**BoardTeams._user(user), "sources": [source for source, _, members, _ in expanded if user_id in members]}
            for user_id, user in sorted(eligible_users.items())
        ]
        roles = db.exec(
            SqlBuilder.select.table(ProjectRole)
            .where(ProjectRole.project_id == project.id, ProjectRole.user_id.in_(ids | {actor.id}))
            .order_by(ProjectRole.id)
        ).all()
        contexts = []
        for user in [actor, *users]:
            context = self.service.card.resolve_visibility_context(project.id, user, channel)
            contexts.append([user.get_uid(), asdict(context[1]) if context else None])
        # Include underlying native internal-audience policy facts, not a client assertion.
        policy = db.exec(SqlBuilder.select.table(EmployeeMembershipPolicy).order_by(EmployeeMembershipPolicy.id)).all()
        links = db.exec(
            SqlBuilder.select.table(UserIdentityLink)
            .where(UserIdentityLink.user_id.in_(ids | {actor.id}))
            .order_by(UserIdentityLink.id)
        ).all()
        scim = db.exec(
            SqlBuilder.select.table(ScimGroupMember)
            .where(ScimGroupMember.user_id.in_(ids | {actor.id}))
            .order_by(ScimGroupMember.id)
        ).all()
        authority_groups = db.exec(
            SqlBuilder.select.table(ScimGroup)
            .where(ScimGroup.id.in_({membership.group_id for membership in scim}))
            .order_by(ScimGroup.id)
        ).all()
        settings = db.exec(
            SqlBuilder.select.table(SettingRole).where(SettingRole.user_id == actor.id).order_by(SettingRole.id)
        ).all()
        organizations = db.exec(SqlBuilder.select.table(Organization).order_by(Organization.id)).all()
        membership_revision = _hash(
            {"sources": raw, "assignments": _facts(assignments), "owner_id": str(project.owner_id)}
        )
        policy_revision = _hash(
            {
                "authorization": f"{source_kind}_response_candidate",
                "channel": channel.value,
                "project": project.model_dump(mode="json"),
                "card_revision": card_target_revision(card),
                "users": [_principal(user) for user in users],
                "actor": _principal(actor),
                "roles": _facts(roles),
                "contexts": contexts,
                "employee_policy": _facts(policy),
                "identity_links": _facts(links),
                "scim_memberships": _facts(scim),
                "scim_authorities": _facts(authority_groups),
                "settings": _facts(settings),
                "organizations": _facts(organizations),
                "internal_mode": Env.CARD_INTERNAL_ACCESS_MODE,
                "scim_issuer": Env.SCIM_ISSUER,
                "employee_group_ids": Env.MCP_EMPLOYEE_GROUP_IDS,
                "internal_issuers": Env.CARD_INTERNAL_OIDC_ISSUERS,
                "abilities": abilities,
            }
        )
        result = {
            "snapshot_version": 1,
            "source_kind": source_kind,
            "authorization": f"{source_kind}_response_candidate",
            "project_uid": project_uid,
            "card_uid": card_uid,
            "actor_uids": [record["uid"] for record in actors],
            "actors": actors,
            "sources": summaries,
            "eligible_count": len(actors),
            "excluded_count": len(ids - eligible_users.keys()),
            "exclusions": {"not_currently_eligible": len(ids - eligible_users.keys())},
            "membership_revision": membership_revision,
            "policy_revision": policy_revision,
            "abilities": abilities,
            "execution_authority": False,
        }
        result["snapshot"] = _hash(result)
        return result

    def candidates(
        self,
        actor,
        project_uid,
        card_uid,
        *,
        source_kind,
        kind,
        query="",
        page=1,
        limit=15,
        channel=CollaborationChannel.Api,
    ):
        self._source_kind(source_kind)
        GroupDirectoryService._validate(page, limit, query)
        if kind not in ("user", "group", "team"):
            raise ValueError("Explicit source kind required")
        with self.flow._transaction(read_only=True) as db:
            actor, project, card, abilities = self._authorized(db, actor, project_uid, card_uid, channel)
            if kind == "user":
                assignment = SqlBuilder.select.column(ProjectAssignedUser.user_id).where(
                    ProjectAssignedUser.project_id == project.id
                )
                rows = db.exec(
                    SqlBuilder.select.table(User)
                    .where(
                        User.activated_at.is_not(None),
                        or_(User.id == project.owner_id, User.id.in_(assignment)),
                        *GroupDirectoryService._search(
                            (User.firstname, User.lastname, User.username, User.email), query
                        ),
                    )
                    .order_by(User.id)
                    .limit(MAX_ACTORS + 1)
                ).all()
                if len(rows) > MAX_ACTORS:
                    raise ValueError("Narrow search to at most 256 current board users")
                eligible = self.flow._authorization(db, [user.get_uid() for user in rows], project.id, card, channel)
                records = [
                    {
                        "kind": "user",
                        "uid": user.get_uid(),
                        "label": user.get_fullname() or user.username or user.get_uid(),
                    }
                    for user in rows
                    if user.get_uid() in eligible
                ]
                total = len(records)
                records = records[(page - 1) * limit : page * limit]
            else:
                if kind == "group" and not abilities["select_group"]:
                    raise DecisionDenied()
                model, field = (ScimGroup, ScimGroup.display_name) if kind == "group" else (BoardTeam, BoardTeam.name)
                filters = GroupDirectoryService._search((field,), query)
                if kind == "team":
                    filters.append(BoardTeam.project_id == project.id)
                total = db.exec(SqlBuilder.select.count(model, model.id).where(*filters)).first()
                rows = db.exec(
                    SqlBuilder.select.table(model)
                    .where(*filters)
                    .order_by(field, model.id)
                    .offset((page - 1) * limit)
                    .limit(limit)
                ).all()
                records = [
                    {"kind": kind, "uid": row.get_uid(), "label": row.display_name if kind == "group" else row.name}
                    for row in rows
                ]
            return {
                "source_kind": source_kind,
                "authorization": f"{source_kind}_response_candidate",
                "records": records,
                "total_records": total,
                "page": page,
                "limit": limit,
                "abilities": abilities,
                "execution_authority": False,
            }
