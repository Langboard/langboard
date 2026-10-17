"""External AppRole selection preserves legacy mounts and verified TLS."""

import importlib
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from ....Env import Env
from .OpenBaoVaultProvider import OpenBaoVaultProvider


@pytest.mark.parametrize("provider_name", ["OpenBao", "HashiCorp"])
@pytest.mark.parametrize("ca", ["", "/run/certs/vault-ca.pem"])
def test_external_client_receives_credentials_ca_and_timeout(monkeypatch, provider_name, ca):
    module = importlib.import_module(f"{__package__}.{provider_name}VaultProvider")
    fake_env = SimpleNamespace(
        KEY_PROVIDER_OPENBAO_URL="https://vault.invalid",
        KEY_PROVIDER_OPENBAO_ROLE_ID="fixture-role",
        KEY_PROVIDER_OPENBAO_SECRET_ID="fixture-secret",
        KEY_PROVIDER_HASHICORP_URL="https://vault.invalid",
        KEY_PROVIDER_HASHICORP_ROLE_ID="fixture-role",
        KEY_PROVIDER_HASHICORP_SECRET_ID="fixture-secret",
        KEY_PROVIDER_VAULT_CA_CERT=ca,
        KEY_PROVIDER_VAULT_TIMEOUT=7,
    )
    client = Mock()
    client.auth.approle.login.return_value = {"auth": {"client_token": "fixture-token"}}
    factory = Mock(return_value=client)
    monkeypatch.setattr(module, "Env", fake_env)
    monkeypatch.setattr(module, "Client", factory)
    getattr(module, f"{provider_name}VaultProvider")()
    factory.assert_called_once_with(url="https://vault.invalid", verify=ca or True, timeout=7)
    client.auth.approle.login.assert_called_once_with("fixture-role", "fixture-secret")


@pytest.mark.parametrize("role,secret", [("fixture-role", ""), ("", "fixture-secret")])
def test_partial_explicit_credentials_do_not_fall_back_to_mounted_file(monkeypatch, tmp_path, role, secret):
    module = importlib.import_module(f"{__package__}.OpenBaoVaultProvider")
    (tmp_path / ".vault-credentials").write_text("VAULT_ROLE_ID=old\nVAULT_SECRET_ID=old\n")
    monkeypatch.setattr(
        module,
        "Env",
        SimpleNamespace(
            ROOT_DIR=tmp_path,
            KEY_PROVIDER_OPENBAO_ROLE_ID=role,
            KEY_PROVIDER_OPENBAO_SECRET_ID=secret,
        ),
    )
    with pytest.raises(ValueError, match="Both OpenBao"):
        object.__new__(OpenBaoVaultProvider)._load_vault_credentials()


def test_existing_credentials_file_remains_compatible(monkeypatch, tmp_path):
    module = importlib.import_module(f"{__package__}.OpenBaoVaultProvider")
    (tmp_path / ".vault-credentials").write_text("VAULT_ROLE_ID=legacy-role\nVAULT_SECRET_ID=legacy-secret\n")
    monkeypatch.setattr(
        module,
        "Env",
        SimpleNamespace(
            ROOT_DIR=tmp_path,
            KEY_PROVIDER_OPENBAO_ROLE_ID="",
            KEY_PROVIDER_OPENBAO_SECRET_ID="",
        ),
    )
    assert object.__new__(OpenBaoVaultProvider)._load_vault_credentials() == ("legacy-role", "legacy-secret")


@pytest.mark.parametrize("value", ["0", "121", "invalid"])
def test_invalid_timeout_fails_before_connection(monkeypatch, value):
    monkeypatch.setitem(Env._Env__envs, "KEY_PROVIDER_VAULT_TIMEOUT", value)
    with pytest.raises(ValueError):
        _ = Env.KEY_PROVIDER_VAULT_TIMEOUT


@pytest.mark.parametrize(
    "provider_type,environment,copy_expected",
    [
        ("openbao-local", "development", True),
        ("openbao-local", "production", False),
        ("openbao-external", "development", False),
        ("openbao-external", "production", False),
    ],
)
def test_generated_external_keys_never_create_a_plaintext_local_copy(
    monkeypatch, provider_type, environment, copy_expected
):
    module = importlib.import_module(f"{__package__}.OpenBaoVaultProvider")
    monkeypatch.setattr(module, "Env", SimpleNamespace(KEY_PROVIDER_TYPE=provider_type, ENVIRONMENT=environment))
    provider = object.__new__(OpenBaoVaultProvider)
    provider.store_secret = Mock()
    provider._save_key_to_file = Mock()
    material = provider.create_key("fixture")
    provider.store_secret.assert_called_once_with("fixture", material)
    assert provider._save_key_to_file.called is copy_expected
