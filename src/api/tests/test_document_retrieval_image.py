import json
from pathlib import Path


def test_api_base_installs_retrieval_without_document_inference():
    dockerfile = (Path(__file__).parents[3] / "Dockerfile").read_text()
    base = dockerfile.split("FROM base AS with-aws", 1)[0]
    assert "uv sync --locked --no-dev --extra document-retrieval" in base
    assert "--extra document-processing" not in base
    assert "fonts-noto-cjk" not in base


def test_worker_release_selects_document_processing_stage():
    root = Path(__file__).parents[3]
    profiles = json.loads((root / ".fractalops/image-build.json").read_text())["profiles"]
    target = profiles["worker"]["buildTarget"]
    dockerfile = (root / profiles["worker"]["dockerfile"]).read_text()
    stage = dockerfile.split(f"FROM base AS {target}", 1)[1].split("FROM base AS", 1)[0]
    assert "--extra document-processing" in stage
    assert "fonts-noto-cjk" in stage
    assert "UV_NO_SYNC=1" in stage
    assert "USER 10001:10001" in stage
