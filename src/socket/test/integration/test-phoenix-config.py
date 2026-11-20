import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    shell = sys.argv[1]
    root = Path(__file__).resolve().parents[4]
    nginx_config = (root / "docker" / "server" / "nginx.conf").read_text(encoding="utf-8")
    access_log_format = re.search(r"log_format\s+main\s+(.+?);", nginx_config, re.DOTALL)
    assert access_log_format is not None
    assert "$request_method $uri $server_protocol" in access_log_format.group(1)
    assert not any(
        variable in access_log_format.group(1)
        for variable in ("$request_uri", "$query_string", "$args", "$http_referer")
    )
    assert re.search(r"\$request(?!_)", access_log_format.group(1)) is None

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    for secret, internal_url, editor_url in (
        ("", "", ""),
        ("too-short", "", ""),
        ("s" * 32, "", ""),
        ("s" * 32, "https://realtime.example.invalid/internal", "https://editor.example.invalid"),
        ("s" * 32, "http://realtime.example.invalid:5690", ""),
    ):
        with tempfile.TemporaryDirectory(prefix="phoenix-config-") as directory:
            fixture = Path(directory)
            scripts = fixture / "scripts" / "utils"
            scripts.mkdir(parents=True)
            for name in ("update-docker-envs.sh", "update-docker-envs.ps1", "utils.sh"):
                shutil.copyfile(root / "scripts" / "utils" / name, scripts / name)
            output = fixture / "docker" / "envs"
            output.mkdir(parents=True)
            sentinel = output / ".api.env"
            sentinel.write_text("unchanged\n", encoding="utf-8")
            for template in ("server-common", "server", "db-backup"):
                (output / f"{template}.env.template").write_text(
                    "SOCKET_HOST=${SOCKET_HOST}\nSOCKET_PORT=${SOCKET_PORT}\n"
                    "MAX_REQUEST_BODY_SIZE_BYTES=${MAX_REQUEST_BODY_SIZE_BYTES}\n",
                    encoding="utf-8",
                )
            for template in ("server-common", "server"):
                shutil.copyfile(
                    root / "docker" / "envs" / f"{template}.env.template", output / f"{template}.env.template"
                )
            settings = {
                "PROJECT_NAME": "fixture",
                "MAX_FILE_SIZE_MB": "50",
                "COMMON_SECRET_KEY": "fixture-common-signing-key",
                "JWT_SECRET_KEY": "fixture-jwt-signing-key",
                "SOCKET_PHOENIX_INTERNAL_SECRET": secret,
                "SOCKET_PHOENIX_KAFKA_ENABLED": "true",
                "NOTIFICATION_EMAIL_OUTBOX_ENABLED": "true",
                "MAIL_SERVER": "localhost",
                "MAIL_FROM": "fixture@example.invalid",
                "MAIL_PORT": "1025",
                "BROADCAST_PHOENIX_FANOUT_CONSUMER_GROUP": "fixture-phoenix",
                "SOCKET_PHOENIX_BOARD_CHAT_SEND_ENABLED": "true",
                "SOCKET_PHOENIX_BOARD_CHAT_RESUME_ENABLED": "true",
                "SOCKET_PHOENIX_BOARD_CHAT_RECOVERY_ENABLED": "true",
                "SOCKET_PHOENIX_EDITOR_AI_ENABLED": "true",
                "SOCKET_PHOENIX_EDITOR_SYNC_ENABLED": "true",
                "SOCKET_PHOENIX_EDITOR_SYNC_EXPECTED_NODES": "1",
                "SOCKET_INTERNAL_URL": internal_url,
                "SOCKET_EDITOR_INTERNAL_URL": editor_url,
                "SSL_CERT_FILE": "/fixture/ca-bundle.pem" if internal_url else "",
                "SSL_CERT_DIR": "/fixture/ca-directory" if internal_url else "",
            }
            (fixture / ".env").write_text("\n".join(f"{key}={value}" for key, value in settings.items()))
            clean_env = {key: value for key, value in os.environ.items() if key not in settings}
            commands = [[shell, str(scripts / "update-docker-envs.sh")]]
            if powershell:
                commands.append([powershell, "-NoProfile", "-File", str(scripts / "update-docker-envs.ps1")])
            for command in commands:
                sentinel.write_text("unchanged\n", encoding="utf-8")
                result = subprocess.run(
                    command, cwd=fixture, capture_output=True, text=True, timeout=30, check=False, env=clean_env
                )
                if len(secret) < 32:
                    assert result.returncode != 0, result.stderr
                    assert "SOCKET_PHOENIX_INTERNAL_SECRET" in result.stderr, result.stderr
                    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"
                else:
                    assert result.returncode == 0, result.stderr
                    rendered = sentinel.read_text(encoding="utf-8")
                    assert "SOCKET_HOST=fixture_socket_phoenix\n" in rendered
                    assert "SOCKET_PORT=5690\n" in rendered
                    assert "MAX_REQUEST_BODY_SIZE_BYTES=53477376\n" in rendered
                    for service in ("api", "socket", "graph"):
                        values = dict(
                            line.split("=", 1)
                            for line in (output / f".{service}.env").read_text().splitlines()
                            if "=" in line
                        )
                        assert values["SOCKET_INTERNAL_URL"] == (internal_url or "http://fixture_socket_phoenix:5690")
                        assert values["SOCKET_EDITOR_INTERNAL_URL"] == (editor_url or "http://fixture_server:8010")
                        assert values["SSL_CERT_FILE"] == settings["SSL_CERT_FILE"]
                        assert values["SSL_CERT_DIR"] == settings["SSL_CERT_DIR"]
                if secret:
                    assert secret not in result.stdout + result.stderr


if __name__ == "__main__":
    main()
