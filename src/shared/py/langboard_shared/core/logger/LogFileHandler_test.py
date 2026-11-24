import logging
from unittest.mock import patch
from langboard_shared.core.logger.LogFileHandler import LogFileHandler
from langboard_shared.Env import Env


def test_unwritable_log_sink_does_not_abort_work_and_recovers(tmp_path):
    handler = LogFileHandler(tmp_path)
    record = logging.LogRecord(Env.PROJECT_NAME, logging.ERROR, __file__, 1, "request finished", (), None)
    try:
        with patch("builtins.open", side_effect=PermissionError("read-only log directory")), patch.object(
            handler, "handleError"
        ) as report:
            handler.emit(record)
            report.assert_called_once_with(record)
        assert handler._stream is None
        handler.emit(record)
        assert "request finished" in next((tmp_path / "main").glob("*.log")).read_text()
        assert handler._stream is None
    finally:
        handler.close()
