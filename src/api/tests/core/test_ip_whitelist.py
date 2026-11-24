"""Bot and API-key permissions retain wildcard precedence and network normalization."""

import pytest
from langboard_shared.domain.services.factory.ApiKeyService import ApiKeyService
from langboard_shared.domain.services.factory.BotService import BotService


@pytest.mark.parametrize("service", [BotService, ApiKeyService])
@pytest.mark.parametrize(
    "values,expected",
    [
        ([], []),
        (["invalid", "192.0.2.1"], ["192.0.2.1"]),
        (["192.0.2.123/24", "192.0.2.4"], ["192.0.2.0/24", "192.0.2.4"]),
        (["invalid", "*", "192.0.2.1"], ["*"]),
        (["192.0.2.1", "192.0.2.1"], ["192.0.2.1", "192.0.2.1"]),
    ],
)
def test_both_permission_entrypoints_preserve_whitelist(values, expected, service):
    original = values.copy()
    assert service.filter_valid_ip_whitelist(values) == expected
    assert values == original
