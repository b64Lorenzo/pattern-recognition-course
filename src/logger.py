"""Small reusable per-run logger for course experiments."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
import sys
import threading


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    OFF = 50


def _one_line(value: object) -> str:
    """Escape delimiters and controls so each record remains one line."""
    text = str(value)
    result: list[str] = []
    for char in text:
        if char == "\\":
            result.append("\\\\")
        elif char == "\n":
            result.append("\\n")
        elif char == "\r":
            result.append("\\r")
        elif char == "|":
            result.append("\\|")
        elif ord(char) < 32 or ord(char) == 127:
            result.append("?")
        else:
            result.append(char)
    return "".join(result)


class Logger:
    """Write UTC, pipe-delimited events to a unique run log and stderr."""

    _sequence = 0
    _sequence_lock = threading.Lock()

    def __init__(self, minimum_level: LogLevel = LogLevel.INFO,
                 log_root: str | Path = "logs") -> None:
        try:
            self.minimum_level = LogLevel(minimum_level)
        except ValueError as exc:
            raise ValueError("Unknown minimum log level") from exc
        if not str(log_root):
            raise ValueError("Log directory must not be empty")

        root = Path(log_root)
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            with self._sequence_lock:
                sequence = Logger._sequence
                Logger._sequence += 1
            candidate = root / f"{stamp}-{sequence}"
            try:
                candidate.mkdir()
                self.run_directory = candidate
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Could not create a unique run directory")

        self.log_file = self.run_directory / "run.log"
        try:
            self._file = self.log_file.open("x", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise RuntimeError("Could not open run log for writing") from exc
        self._lock = threading.Lock()

    def write(self, level: LogLevel, component: str, message: str) -> None:
        try:
            level = LogLevel(level)
        except ValueError as exc:
            raise ValueError("Unknown log level") from exc
        if level == LogLevel.OFF or self.minimum_level == LogLevel.OFF or level < self.minimum_level:
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = "|".join((timestamp, level.name, _one_line(component), _one_line(message)))
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()
            if not self._file:
                raise RuntimeError("Could not write to run log")
            print(line, file=sys.stderr)

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Logger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
