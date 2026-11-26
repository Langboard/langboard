"""The Dokploy canary must reuse the retained OpenBao network."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]


def test_python_canary_uses_retained_openbao_network_for_all_python_services():
    compose = yaml.safe_load((ROOT / "docker/python-canary-development.compose.yaml").read_text())

    assert compose["networks"]["langboard_network"] == {
        "external": True,
        "name": "langboard_network",
    }
    for service_name in ("api", "worker", "graph"):
        service = compose["services"][service_name]
        assert "langboard_network" in service["networks"]
        assert service["environment"]["KEY_PROVIDER_OPENBAO_URL"] == "http://langboard_vault:8200"
