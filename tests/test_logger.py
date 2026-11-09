import io
from contextlib import redirect_stderr
from pathlib import Path
import tempfile
import unittest

from src.logger import LogLevel, Logger


class LoggerTests(unittest.TestCase):
    def test_writes_utc_line_to_file_and_stderr_with_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with Logger(log_root=Path(temporary)) as logger:
                    logger.write(LogLevel.INFO, "model|fit", "first\nsecond")
                    log_file = logger.log_file

            line = log_file.read_text(encoding="utf-8").strip()
            self.assertRegex(line, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\|INFO\|model\\\|fit\|first\\nsecond$")
            self.assertEqual(stderr.getvalue().strip(), line)

    def test_filters_below_minimum_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with Logger(LogLevel.WARNING, temporary) as logger:
                    logger.write(LogLevel.INFO, "test", "filtered")
                    logger.write(LogLevel.ERROR, "test", "kept")
            records = next(Path(temporary).glob("*/run.log")).read_text(encoding="utf-8")
            self.assertNotIn("filtered", records)
            self.assertIn("|ERROR|test|kept", records)
            self.assertNotIn("filtered", stderr.getvalue())

    def test_each_logger_gets_a_distinct_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Logger(log_root=temporary)
            second = Logger(log_root=temporary)
            try:
                self.assertNotEqual(first.run_directory, second.run_directory)
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
