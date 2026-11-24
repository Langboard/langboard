"""Selection must use the resolved value, not any matching line in .env."""

import os
import shutil
import subprocess
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize(
    "provider,bundled",
    [
        ("", True),
        ("openbao-local", True),
        ("OPENBAO-LOCAL", True),
        ("openbao-external", False),
        ("hashicorp", False),
        ("azure", False),
        ("aws", False),
    ],
)
def test_compose_selects_only_bundled_vault(tmp_path, provider, bundled):
    # The second assignment overrides the first, as it does for the runtime.
    (tmp_path / ".env").write_text(f'KEY_PROVIDER_TYPE=openbao-local\nKEY_PROVIDER_TYPE="{provider}"\n')
    result = subprocess.run(
        [shutil.which("bash"), str(ROOT / "scripts/utils/get-compose-args.sh")],
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert ("docker-compose.vault.yaml" in result.stdout) is bundled
