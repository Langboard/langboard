import logging
from io import StringIO
from langboard_shared.core.logger import Logger
from langboard_shared.core.logger.LogFileHandler import LogFileHandler
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text


def test_exception_handlers_keep_tracebacks_without_frame_locals(tmp_path):
    config = Logger.get_config()
    handlers = Logger.get_handlers()
    try:
        rich_handlers = [handlers[0]]
        for name in ("default", "access"):
            options = {key: value for key, value in config["handlers"][name].items() if key not in ("class", "formatter")}
            rich_handlers.append(RichHandler(**options))
        rich_handlers.append(LogFileHandler(tmp_path, is_terminal=True))

        for handler in rich_handlers:
            output = StringIO()
            handler.console = Console(file=output, width=120, color_system=None)
            logger = logging.Logger("langboard.traceback-regression")
            logger.addHandler(handler)
            try:
                credential = "_".join(("PRIVATE", "FRAME", "LOCAL", "MUST", "NOT", "BE", "RENDERED"))
                raise RuntimeError("traceback regression sentinel")
            except RuntimeError:
                logger.exception("Request failed")
            rendered = (
                Text.from_markup((tmp_path / "traceback-regression" / "terminal.log").read_text()).plain
                if isinstance(handler, LogFileHandler)
                else output.getvalue()
            )
            assert "RuntimeError: traceback regression sentinel" in rendered
            assert "Traceback" in rendered
            assert credential[:13] not in rendered
    finally:
        for handler in handlers:
            handler.close()
