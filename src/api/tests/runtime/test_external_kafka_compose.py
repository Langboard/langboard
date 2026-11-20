"""Render native external selection; no service, broker or volume is started."""

import json
import os
import shlex
import shutil
import subprocess
import pytest
from test_external_redis_compose import ROOT, selected_args


@pytest.mark.parametrize("external", [False, True])
def test_native_kafka_selection_and_certificate_transport(tmp_path, external):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    values = {
        "BROADCAST_URLS": "broker.invalid:9093",
        "BROADCAST_KAFKA_SECURITY_PROTOCOL": "SASL_SSL",
        "BROADCAST_KAFKA_SSL_CAFILE": "/run/langboard/kafka/ca.pem",
        "BROADCAST_KAFKA_SASL_MECHANISM": "SCRAM-SHA-256",
        "BROADCAST_KAFKA_SASL_USERNAME": "fixture-user",
        "BROADCAST_KAFKA_SASL_PASSWORD": "fixture-password",
    }
    selected = selected_args(tmp_path, values if external else {})
    assert selected.returncode == 0, selected.stderr
    args = shlex.split(selected.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            args[index] = str(ROOT / value)
    if external:
        args += ["-f", str(ROOT / "docker/docker-compose.kafka-external.yaml")]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(ROOT),
            *args,
            "config",
            "--no-env-resolution",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env={**os.environ, **(values if external else {})},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    bundled = {"kafka0", "kafka1", "kafka2", "kafka-volume-permissions"}
    assert bool(bundled.intersection(services)) is not external
    for name in ("api", "celeryworker", "graph", "socket-phoenix"):
        service = services[name]
        assert any(mount["target"] == "/run/langboard/kafka" and mount["read_only"] for mount in service["volumes"])
        if external:
            assert not bundled.intersection(service.get("depends_on", {}))
            for key, value in values.items():
                assert service["environment"][key] == value
        else:
            assert bundled - {"kafka-volume-permissions"} <= service["depends_on"].keys()
