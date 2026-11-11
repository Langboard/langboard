import base64
import importlib.util
import json
import subprocess
from pathlib import Path
import pytest


# Load the pure FFmpeg adapter without initializing the API application.
SOURCE = Path(__file__).parents[2] / "langboard/media/storyboard.py"
SPEC = importlib.util.spec_from_file_location("storyboard_under_test", SOURCE)
assert SPEC is not None and SPEC.loader is not None
storyboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storyboard)


def test_real_video_produces_decodable_sprite_and_bounded_webvtt(tmp_path):
    result = storyboard.generate_storyboard(Path(__file__).parent / "fixtures/fixture.mp4")
    assert result["available"] is True
    assert 3 < result["duration"] < 4
    assert result["vtt"].startswith("WEBVTT\n")
    assert result["vtt"].count("-->") == 24
    assert "storyboard.jpg#xywh=800,270,160,90" in result["vtt"]
    sprite = tmp_path / "sprite.jpg"
    sprite.write_bytes(base64.b64decode(result["sprite_base64"], validate=True))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json", str(sprite)],
        check=True,
        capture_output=True,
    )
    assert json.loads(probe.stdout)["streams"] == [{"width": 960, "height": 360}]


@pytest.mark.parametrize("payload", [b"not media", b"#EXTM3U\nhttp://127.0.0.1:9/private\n"])
def test_invalid_media_and_network_playlists_are_not_supported(tmp_path, payload):
    source = tmp_path / "input.mp4"
    source.write_bytes(payload)
    with pytest.raises(storyboard.StoryboardUnavailable):
        storyboard.generate_storyboard(source)


def test_missing_media_tools_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(storyboard.shutil, "which", lambda _: None)
    with pytest.raises(storyboard.StoryboardUnavailable):
        storyboard.generate_storyboard(tmp_path / "missing.mp4")
