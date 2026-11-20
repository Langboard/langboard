"""Use the native board fixture with real primary sessions."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_spec = spec_from_file_location(
    "project_document_native_board_fixture", Path(__file__).parents[1] / "card_cross_board/test_card_mirrors.py"
)
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
mirror_board = _module.mirror_board
