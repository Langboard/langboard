"""Exercise real shell producer and one immutable compiled constants consumer."""

import hashlib
import json
import os
import subprocess
import pytest
from test_external_redis_compose import ROOT
from test_prebuilt_compose import render


@pytest.fixture(scope="module")
def consumer(tmp_path_factory):
    output = tmp_path_factory.mktemp("ui-runtime") / "consumer.js"
    command = [
        str(ROOT / "src/ui/node_modules/.bin/esbuild"),
        "src/constants.ts",
        "--bundle",
        "--tsconfig=tsconfig.app.json",
        "--format=iife",
        "--global-name=AppConfig",
        f"--outfile={output}",
        '--define:process.env.IS_PRODUCTION="true"',
    ]
    for key, value in {
        "API_URL": "https://built-api.example.invalid",
        "PUBLIC_UI_URL": "https://built-ui.example.invalid",
        "SOCKET_URL": "wss://built-realtime.example.invalid",
    }.items():
        command.append(f"--define:process.env.{key}={json.dumps(value)}")
    subprocess.run(command, cwd=ROOT / "src/ui", check=True, capture_output=True, text=True)
    return output


def run_config(tmp_path, consumer, values):
    generated = tmp_path / "runtime-config.js"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LANGBOARD_UI_")}
    environment.update(values)
    subprocess.run(["sh", str(ROOT / "docker/ui-runtime-config.sh"), str(generated)], env=environment, check=True)
    result = subprocess.run(
        [
            "node",
            "--input-type=commonjs",
            "-e",
            """
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({URL, process: {env: {}}});
try {
    vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
    vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
    console.log(JSON.stringify({ API_URL: context.AppConfig.API_URL, PUBLIC_UI_URL: context.AppConfig.PUBLIC_UI_URL, SOCKET_URL: context.AppConfig.SOCKET_URL }));
} catch (error) { console.error(error.message); process.exitCode = 1; }
if (context.injected) process.exitCode = 2;
""",
            str(generated),
            str(consumer),
        ],
        capture_output=True,
        text=True,
    )
    return result, generated.read_text()


def test_one_compiled_consumer_selects_two_environments_without_rebuild(tmp_path, consumer):
    digest = hashlib.sha256(consumer.read_bytes()).hexdigest()
    for name in ("staging", "production"):
        endpoints = {
            "API_URL": f"https://{name}-api.example.invalid/api",
            "PUBLIC_UI_URL": f"https://{name}-ui.example.invalid",
            "SOCKET_URL": f"wss://{name}-realtime.example.invalid/socket",
        }
        result, _ = run_config(tmp_path, consumer, {f"LANGBOARD_UI_{key}": value for key, value in endpoints.items()})
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == endpoints
        assert hashlib.sha256(consumer.read_bytes()).hexdigest() == digest
    result, _ = run_config(tmp_path, consumer, {})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["API_URL"] == "https://built-api.example.invalid"


@pytest.mark.parametrize(
    "value",
    [
        'https://host/";globalThis.injected=true;//',
        "https://host/'\\",
        "https://host/</script><script>globalThis.injected=true</script>",
        "https://host/\n",
        "https://u:p@host",
        "https://host?query",
        "https://host#fragment",
        "https://",
        "ftp://host",
    ],
)
def test_shell_input_cannot_execute_or_fall_back(tmp_path, consumer, value):
    result, generated = run_config(tmp_path, consumer, {"LANGBOARD_UI_API_URL": value})
    assert result.returncode == 1
    assert result.stderr.strip() == "Invalid public runtime endpoint: API_URL"
    assert value not in generated
    assert not result.stdout


def test_bootstrap_native_startup_and_cache_contract():
    index = (ROOT / "src/ui/index.html").read_text()
    assert index.index("globalThis.__LANGBOARD_RUNTIME_CONFIG__ = { invalid: true }") < index.index(
        'src="/runtime-config.js"'
    )
    assert index.index('src="/runtime-config.js"') < index.index('type="module"')
    assert '<script src="/runtime-config.js"></script>' in index
    dockerfile = (ROOT / "docker/ui-production.Dockerfile").read_text().split(" AS runtime\n", 1)[1]
    assert "ENTRYPOINT" not in dockerfile and "CMD" not in dockerfile
    assert "COPY --chmod=755 docker/ui-runtime-config.sh /docker-entrypoint.d/40-ui-runtime-config.sh" in dockerfile
    assert dockerfile.index("40-ui-runtime-config.sh") < dockerfile.index("USER 101:101")
    config = (ROOT / "docker/ui-nginx.conf").read_text()
    route = config.split("location = /runtime-config.js {", 1)[1].split("}", 1)[0]
    assert "alias /tmp/langboard-runtime-config.js;" in route
    assert 'Cache-Control "no-store, no-cache, must-revalidate" always;' in route
    assert "expires off;" in route and "try_files" not in route
    assert 'Cache-Control "public, max-age=31536000, immutable"' in config
    main = (ROOT / "src/ui/src/main.tsx").read_text()
    assert 'import { PUBLIC_UI_URL } from "@/constants"' in main
    assert "new URL(PUBLIC_UI_URL)" in main and "process.env.PUBLIC_UI_URL" not in main


def test_compose_optional_public_environment_inputs(tmp_path):
    result = render(tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["services"]["ui"]["environment"] == {
        f"LANGBOARD_UI_{key}": "" for key in ("API_URL", "PUBLIC_UI_URL", "SOCKET_URL")
    }
