"""Existing native database fixture; API response binding projection regression."""

from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import EditorGraphApprovalRequest
from test_decision_flow import native as policy_native  # noqa: F401
from test_decision_hitl import bound, policy  # noqa: F401


def test_persisted_binding_api_boolean_preserves_native_fields(bound):  # noqa: F811
    fixture, _, _, approval = bound
    service = fixture[1].service

    def reread():
        current = service.graph_approval_request.repo.graph_approval_request.get_by_id_like(approval.id)
        with DbSession.use(readonly=True) as db:
            detail = db.exec(
                SqlBuilder.select.table(EditorGraphApprovalRequest).where(
                    EditorGraphApprovalRequest.approval_request_id == current.id
                )
            ).first()
        return current, service.graph_approval_request.get_api_response(current, detail)

    current, before = reread()
    assert before["decision_bound"] is False
    assert before["durable_run"] is True
    for key, value in current.api_response().items():
        assert before[key] == value
    policy(bound)
    current, after = reread()
    assert current.decision_case_id is not None
    assert after["decision_bound"] is True
    assert after["durable_run"] is True
    assert before["uid"] == after["uid"]
