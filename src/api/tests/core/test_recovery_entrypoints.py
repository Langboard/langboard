"""Direct cron entrypoints reuse recovery behavior without CLI command discovery."""

import importlib
import runpy
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from langboard_shared.domain.models.InternalBotRun import InternalBotRunKind
from langboard_shared.domain.services import DomainService


@pytest.mark.parametrize("kind", ["OllamaModelPull", "InternalBotRun"])
@pytest.mark.parametrize("fails", [False, True])
def test_direct_recovery_module_executes_existing_service_and_preserves_failure(monkeypatch, kind, fails):
    recovered = Mock(
        return_value=[
            SimpleNamespace(kind=InternalBotRunKind.BoardChat, get_uid=lambda: "board-run"),
            SimpleNamespace(kind=InternalBotRunKind.EditorChat, get_uid=lambda: "editor-run"),
        ]
    )
    if fails:
        recovered.side_effect = RuntimeError("Recovery failed")
    service = SimpleNamespace(
        ollama_model_pull=SimpleNamespace(recover=recovered),
        internal_bot_run=SimpleNamespace(recover_expired=recovered),
    )
    monkeypatch.setattr(DomainService, "use", lambda: nullcontext(service))
    reconcile = Mock()
    attachment = importlib.import_module("langboard_shared.ai.BoardChatAttachment")
    monkeypatch.setattr(attachment, "schedule_board_chat_attachment_reconciliation", reconcile)
    module = f"langboard.commands.Run{kind}RecoveryCommand"
    # Other runtime suites may have already imported the manual CLI command.
    monkeypatch.delitem(sys.modules, module, raising=False)
    if fails:
        with pytest.raises(RuntimeError, match="Recovery failed"):
            runpy.run_module(module, run_name="__main__")
    else:
        runpy.run_module(module, run_name="__main__")
    recovered.assert_called_once_with()
    if kind == "InternalBotRun" and not fails:
        reconcile.assert_called_once_with("board-run")
    else:
        reconcile.assert_not_called()
