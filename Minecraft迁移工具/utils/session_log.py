"""Append-only session logs and a bounded, thread-safe tail for live views."""
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from utils.config import CONFIG_FILE


class SessionLog:
    def __init__(self, directory=None, limit=5000):
        self.directory = Path(directory) if directory is not None else Path(CONFIG_FILE).parent / "migration_logs"
        self.limit = limit
        self.lines = deque(maxlen=limit)
        self.sequence = 0
        self.generation = 0
        self.path = None
        self._file = None
        self._lock = threading.RLock()
        self.error = None

    def append(self, message, level="INFO", save=True):
        with self._lock:
            if save:
                try:
                    if self._file is None:
                        self.directory.mkdir(parents=True, exist_ok=True)
                        if self.path is None:
                            name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".txt"
                            self.path = self.directory / name
                        self._file = self.path.open("a", encoding="utf-8", buffering=1)
                    self._file.write(message + "\n")
                except OSError as error:
                    self.error = str(error)
            for line in message.split("\n"):
                self.sequence += 1
                self.lines.append((self.sequence, level, line + "\n"))

    def snapshot(self, after=0):
        with self._lock:
            # Walk only the new tail during ordinary updates.
            rows = []
            for row in reversed(self.lines):
                if row[0] <= after:
                    break
                rows.append(row)
            rows.reverse()
            return self.generation, self.sequence, rows

    def status(self):
        with self._lock:
            return self.generation, self.sequence

    def clear(self):
        """Clear the live display; the on-disk session remains complete."""
        with self._lock:
            self.lines.clear()
            self.generation += 1

    def flush(self):
        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self):
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None
