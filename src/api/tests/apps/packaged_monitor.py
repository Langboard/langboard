"""Load the reviewed independent app artifact, isolated from earlier test imports."""

import hashlib
import json
import sys
from pathlib import Path


def load_monitor(monkeypatch, provenance_name):
    vendor = Path(__file__).parent / "vendor"
    provenance = json.loads((vendor / provenance_name).read_text())
    artifact = vendor / provenance["artifact"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == provenance["sha256"]
    monkeypatch.delitem(sys.modules, "example_monitor", raising=False)
    monkeypatch.syspath_prepend(str(artifact))
    import example_monitor

    assert str(artifact) in example_monitor.__file__
    return example_monitor.Monitor
