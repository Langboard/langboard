"""Kafka protocol probes must be cheap at rest without extending failure budgets."""

import json
import shlex
import shutil
import subprocess
import pytest
from test_external_redis_compose import EXTERNAL, ROOT, selected_args


@pytest.mark.parametrize("external_redis", [False, True])
def test_rendered_kafka_protocol_probe_preserves_startup_and_detection_budget(tmp_path, external_redis):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is required for configuration rendering")
    selected = selected_args(tmp_path, EXTERNAL if external_redis else {})
    assert selected.returncode == 0, selected.stderr
    args = shlex.split(selected.stdout)
    for index, value in enumerate(args):
        if index and args[index - 1] == "-f":
            args[index] = str(ROOT / value)
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
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    for index in range(3):
        name = f"kafka{index}"
        broker = services[name]
        probe = broker["healthcheck"]
        assert probe["test"] == [
            "CMD-SHELL",
            "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server 127.0.0.1:9092 >/dev/null 2>&1",
        ]

        # Compose normalizes durations; allow units without relying on a YAML parser's inference.
        def seconds(key):
            return float(probe[key][:-1]) * {"s": 1, "m": 60}[probe[key][-1]]

        interval, timeout = seconds("interval"), seconds("timeout")
        assert interval >= 30, "steady-state JVM probes must not run every five seconds"
        assert seconds("start_interval") == 5
        assert seconds("start_period") == 20
        assert timeout == 10
        assert probe["retries"] >= 3, "retain consecutive-failure tolerance"
        assert interval * probe["retries"] == 120, "retain the original immediate-failure detection budget"
        assert (interval + timeout) * probe["retries"] <= 160, "bound the timed-out-probe detection budget"
        assert broker["environment"]["KAFKA_NODE_ID"] == str(index + 1)
        assert broker["volumes"][0]["source"] == f"kfdata{index}"
        assert broker["volumes"][0]["target"] == "/var/lib/kafka/data"
        assert broker["depends_on"]["kafka-volume-permissions"]["condition"] == "service_completed_successfully"
        assert services["api"]["depends_on"][name]["condition"] == "service_healthy"
        assert services["socket-phoenix"]["depends_on"][name]["condition"] == "service_healthy"
