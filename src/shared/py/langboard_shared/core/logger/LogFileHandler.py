from logging import NOTSET, LogRecord
from pathlib import Path
from typing import Optional, TextIO
from rich.logging import RichHandler
from rich.segment import Segment
from rich.text import Text
from rich.traceback import Traceback
from ...Env import Env
from ..types import SafeDateTime
from ..utils.String import concat


class LogFileHandler(RichHandler):
    """A custom logging handler that writes logs to a file."""

    def __init__(self, log_dir: str | Path, level: int = NOTSET, is_terminal: bool = False):
        self._log_dir = Path(log_dir)
        self._stream: Optional[TextIO] = None
        self._stream_path: Optional[Path] = None

        self._is_terminal = is_terminal
        self._log_dir.mkdir(parents=True, exist_ok=True)

        super().__init__(
            level=level,
            markup=is_terminal,
            show_path=False,
            rich_tracebacks=is_terminal,
            tracebacks_show_locals=False,
        )

    def emit(self, record: LogRecord):
        if record.levelno < self.level:
            return

        try:
            self._emit_record(record)
        except OSError:
            # Match logging.StreamHandler: a failed log sink must not abort the
            # request or background job that produced the record.
            self.handleError(record)
        finally:
            if self._stream:
                try:
                    self._stream.close()
                except OSError:
                    pass
                self._stream = None

    def _emit_record(self, record: LogRecord):

        record_name = record.name
        if record.name == Env.PROJECT_NAME:
            record_name = "main"
        else:
            record_name = record.name.replace(f"{Env.PROJECT_NAME}.", "").split(".")[0]

        log_dir = self._log_dir / record_name
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / self._get_log_file_name()
        if str(self._stream_path) != str(log_file) and self._stream:
            self._stream.close()
            self._stream = None

        if not self._stream:
            self._stream = open(log_file, "a", buffering=1, encoding="utf-8")
            self._stream_path = log_file

        if self._is_terminal:
            message = self._markup(record)
        else:
            message = self.format(record)
            if not message.endswith("\n"):
                message = concat(message, "\n")
            message = Text.from_markup(message).plain

        self._stream.write(f"[{SafeDateTime.fromtimestamp(int(record.created))}] {message}")
        self._stream.flush()

    def _get_log_file_name(self):
        if self._is_terminal:
            return "terminal.log"
        return "{:%Y-%m-%d}.log".format(SafeDateTime.now())

    def _markup(self, record: LogRecord):
        original_width = self.console._width
        self.console._width = 108
        message = self.format(record)
        traceback = None
        if self.rich_tracebacks and record.exc_info and record.exc_info != (None, None, None):
            exc_type, exc_value, exc_traceback = record.exc_info
            assert exc_type is not None
            assert exc_value is not None
            traceback = Traceback.from_exception(
                exc_type,
                exc_value,
                exc_traceback,
                width=self.tracebacks_width,
                code_width=self.tracebacks_code_width,
                extra_lines=self.tracebacks_extra_lines,
                theme=self.tracebacks_theme,
                word_wrap=self.tracebacks_word_wrap,
                show_locals=self.tracebacks_show_locals,
                locals_max_length=self.locals_max_length,
                locals_max_string=self.locals_max_string,
                suppress=self.tracebacks_suppress,
                max_frames=self.tracebacks_max_frames,
            )
            message = record.getMessage()
            if self.formatter:
                record.message = record.getMessage()
                formatter = self.formatter
                if hasattr(formatter, "usesTime") and formatter.usesTime():
                    record.asctime = formatter.formatTime(record, formatter.datefmt)
                message = formatter.formatMessage(record)

        message_renderable = self.render_message(record, message)
        log_renderable = self.render(record=record, traceback=traceback, message_renderable=message_renderable)

        render_hooks = self.console._render_hooks[:]
        with self.console:
            renderables = self.console._collect_renderables(
                (log_renderable,),
                " ",
                "\n",
                justify=None,
                emoji=None,
                markup=None,
                highlight=None,
            )
            for hook in render_hooks:
                renderables = hook.process_renderables(renderables)
            render_options = self.console.options.update()

            new_segments: list[Segment] = []
            extend = new_segments.extend
            render = self.console.render
            for renderable in renderables:
                extend(render(renderable, render_options))

        self.console._width = original_width

        markup = ""
        for seg in new_segments:
            text = Text(seg.text, seg.style if seg.style else "")
            markup = concat(markup, text.markup)

        lines = markup.splitlines(True)
        for i in range(len(lines)):
            lines[i] = concat(str(record.levelno).zfill(4), record.name, "\0", lines[i])
        markup = "".join(lines)

        return markup
