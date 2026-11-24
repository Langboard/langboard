"""Pure factories stay independent of HTTP while native dependency cleanup is preserved."""

import os
import subprocess
import sys
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langboard_shared.core.types import Factory


def test_pure_factory_import_does_not_initialize_http_framework():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from langboard_shared.core.types import Factory; assert 'fastapi' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "SENTRY_DSN": ""},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fails", [False, True])
def test_native_dependencies_close_owned_factories_after_success_or_failure(fails):
    instances = []

    class Service(Factory):
        def __init__(self):
            super().__init__()
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True
            super().close()

    app = FastAPI()

    @app.get("/scope")
    def scoped(first=Service.scope(), second=Service.scope()):
        assert not first.closed and not second.closed
        if fails:
            raise RuntimeError("Endpoint failed")
        return {"created": len(instances)}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/scope")
    assert response.status_code == (500 if fails else 200)
    assert len(instances) == 2
    assert all(item.closed for item in instances)
