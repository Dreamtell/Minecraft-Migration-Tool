"""Read append-only IPC files without deleting concurrent writes or partial lines."""
import json
from pathlib import Path


class JsonlReader:
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0

    def read(self, limit=256):
        messages = []
        try:
            with self.path.open('rb') as stream:
                if stream.seek(0, 2) < self.offset:
                    self.offset = 0
                stream.seek(self.offset)
                count = 0
                while limit is None or count < limit:
                    line = stream.readline()
                    if not line.endswith(b'\n'):
                        break  # Keep the read offset before an unfinished UTF-8/JSON line.
                    self.offset = stream.tell()
                    count += 1
                    try:
                        value = json.loads(line.decode('utf-8'))
                    except (UnicodeError, ValueError):
                        continue
                    if isinstance(value, dict):
                        messages.append(value)
        except OSError:
            pass
        return messages
