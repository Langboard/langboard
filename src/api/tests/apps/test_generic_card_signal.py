# ruff: noqa: F811
"""An independently declared adapter shares native card scope and revocation gates."""

import pytest
from langboard.apps.CardSignal import authorized_signal_scope
from langboard.apps.GitHubManifest import GitHubManifestUnavailable
from langboard_sdk.definition import validate_app_definition
from langboard_shared.core.db import DbSession, SqlBuilder
from langboard_shared.domain.models import AppDefinition, AppSignal, BoardAppBinding
from langboard_shared.domain.services.AppSignalProjection import card_signal_projections
from test_card_signal_projection import scoped  # noqa: F401
from test_github_signal import installation, lifecycle, send, signal_storage  # noqa: F401


@pytest.mark.parametrize("revoke", [None, "resource", "capability", "connection"])
@pytest.mark.parametrize("outcome, expected", [("recovered", "resolved"), ("alert", "failed"), ("other", "unknown")])
def test_independent_adapter_scope_and_revocation(scoped, revoke, outcome, expected):
    state, card, card_binding, _ = scoped
    send(state)
    declaration = validate_app_definition({
        "schema_version": 2, "key": "independent-monitor", "name": "Independent Monitor",
        "version": "0.1.0", "description": "Independent observation fixture",
        "capabilities": ["signals.read"], "resource_types": ["service"],
        "trust": {"authentication_origins": [], "mcp_origins": [], "data_origins": []},
        "signal_policy": {
            "version": 1, "event_types": ["incident.observed"], "resource_types": ["service"],
            "commit_mode": "empty", "time_basis": "observation",
            "outcome_states": {"recovered": "resolved", "alert": "failed"},
        },
    })
    app_key = declaration["key"]
    with DbSession.atomic() as db:
        db.insert(AppDefinition(key=app_key, declaration=declaration, approved_by=state[1][1].id))
        state[2].app_key = app_key
        state[4].resource_type = "service"
        if revoke == "connection":
            state[2].state = "disconnected"
        if revoke == "resource":
            state[4].is_selected = False
        for row in (state[2], state[4]):
            db.update(row)
        binding = db.exec(SqlBuilder.select.table(BoardAppBinding).where(BoardAppBinding.id == state[4].board_binding_id)).first()
        binding.app_key = app_key
        if revoke == "capability":
            binding.granted_capabilities = []
        db.update(binding)
        signal = db.exec(SqlBuilder.select.table(AppSignal).where(AppSignal.resource_id == state[4].id)).first()
        signal.provider = app_key
        signal.event_type = "incident.observed"
        signal.commit_sha = ""
        signal.outcome = outcome
        db.update(signal)
        card_binding.commit_sha = ""
        db.update(card_binding)
        args = (state[0], db, state[1][1], state[1][2].get_uid(), state[2].get_uid(), state[4].get_uid(), signal.get_uid())
        if revoke:
            with pytest.raises(GitHubManifestUnavailable):
                authorized_signal_scope(*args)
        else:
            result = authorized_signal_scope(*args)
            assert result[0].id == signal.id and result[2].id == state[4].id
    projections = card_signal_projections([card])
    if revoke:
        assert projections == {}
    else:
        proof = projections[card.id][0]
        assert proof["state"] == expected and proof["time_basis"] == "observation"
