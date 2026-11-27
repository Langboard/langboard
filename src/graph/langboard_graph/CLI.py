import sys
from pathlib import Path


root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root / "shared/py"))
sys.path.insert(0, str(root / "shared/py/core"))
sys.path.insert(0, str(root / "shared/py/models"))


def execute():
    from .CliExecutor import execute

    execute()
