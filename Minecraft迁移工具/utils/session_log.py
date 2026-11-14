"""Append-only session logs and a bounded, thread-safe tail for live views."""
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from utils.config import CONFIG_FILE


class SessionLog:
    # 每次启动一个新文件；目录里只留最新这么多份（旧版 last_log.txt 有轮转，这里对应补上）
    KEEP_FILES = 30
    # 打开失败（磁盘满 / 没权限）后隔这么久才再试，别每一行都重做一次 mkdir + open
    RETRY_S = 5.0

    def __init__(self, directory=None, limit=5000, keep=None):
        self.directory = Path(directory) if directory is not None else Path(CONFIG_FILE).parent / "migration_logs"
        self.limit = limit
        self.keep = self.KEEP_FILES if keep is None else keep
        self.lines = deque(maxlen=limit)
        self.sequence = 0
        self.generation = 0
        self.path = None
        self._file = None
        self._lock = threading.RLock()
        self.error = None
        self._retry_at = 0.0

    def _prune_old(self):
        """新建会话文件时顺手删掉最旧的，只留 keep 份（含刚建的这份）。失败不要紧。"""
        if not self.keep or self.keep <= 0:
            return
        try:
            files = sorted(p for p in self.directory.glob("*.txt") if p.is_file())
        except OSError:
            return
        for old in files[:max(0, len(files) - self.keep)]:
            if old == self.path:
                continue
            try:
                old.unlink()
            except OSError:
                pass

    def append(self, message, level="INFO", save=True):
        with self._lock:
            if save and (self._file is not None or time.perf_counter() >= self._retry_at):
                try:
                    if self._file is None:
                        # close() 之后还有日志（关窗收尾那几行）会回到同一个文件续写，
                        # 行缓冲，进程退出时由解释器关掉，不会丢行
                        self.directory.mkdir(parents=True, exist_ok=True)
                        新建 = self.path is None
                        if 新建:
                            name = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".txt"
                            self.path = self.directory / name
                        self._file = self.path.open("a", encoding="utf-8", buffering=1)
                        if 新建:
                            self._prune_old()
                    self._file.write(message + "\n")
                except OSError as error:
                    self.error = str(error)
                    self._retry_at = time.perf_counter() + self.RETRY_S
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
