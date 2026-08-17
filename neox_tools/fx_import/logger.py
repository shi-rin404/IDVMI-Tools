from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import traceback
from typing import Iterator


class FxImportLogger:
    """Crash-oriented FX import logger.

    Every write is flushed and fsynced so the last entered operation is useful
    even if Blender exits before Python exception handling can run.
    """

    def __init__(self, log_path: str | Path) -> None:
        self.path = Path(log_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8", buffering=1)
        self._depth = 0

    def close(self) -> None:
        if not self._handle.closed:
            self.write("CLOSE")
            self._handle.close()

    def write(self, event: str, **details) -> None:
        indent = "  " * self._depth
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        detail_text = " ".join(
            f"{key}={_shorten(value)}"
            for key, value in details.items()
            if value is not None
        )
        line = f"{timestamp} {indent}{event}"
        if detail_text:
            line = f"{line} {detail_text}"
        self._handle.write(f"{line}\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def exception(self, event: str, exc: BaseException) -> None:
        self.write(event, error=f"{type(exc).__name__}: {exc}")
        for line in traceback.format_exception(type(exc), exc, exc.__traceback__):
            for traceback_line in line.rstrip().splitlines():
                self.write("TRACE", text=traceback_line)

    @contextmanager
    def scope(self, name: str, **details) -> Iterator[None]:
        self.write(f"ENTER {name}", **details)
        self._depth += 1
        try:
            yield
        except BaseException as exc:
            self._depth = max(0, self._depth - 1)
            self.exception(f"ERROR {name}", exc)
            raise
        else:
            self._depth = max(0, self._depth - 1)
            self.write(f"EXIT {name}")


def _shorten(value) -> str:
    text = str(value).replace("\n", "\\n")
    if len(text) > 260:
        return f"{text[:257]}..."
    return text

